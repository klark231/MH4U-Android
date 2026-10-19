#!/usr/bin/env python3
"""find_services: how the game talks to the 3DS OS (system calls and IPC).

Finds
  * every `svc` (ARM and Thumb) and the small wrapper functions around them;
  * IPC request sites: code that takes the thread command buffer
    (`mrc p15, 0, Rd, c13, c0, 3` + 0x80), stores a constant header in word 0 and ends
    in SendSyncRequest (inline `svc 0x32` or through a wrapper), with where the session
    handle comes from (a global, a field of an object, an argument);
  * the service names asked of srv: (GetServiceHandle 0x00050100, and ConnectToPort for
    "srv:" itself) and which globals the returned session handles are stored in;
and names each request from a built-in table of public 3DS IPC commands (3dbrew).

How: a small abstract interpreter runs over each function's control flow (ARM and
Thumb-1) with symbolic values ("constant", "thread-local + n", "stack slot",
"argument n + k", "word loaded from X", "handle of service S", ...). What a function
stores through its arguments becomes a summary that is applied at its call sites, and
values that depend on arguments are resolved through the direct call graph a few levels
up. Indirect calls (vtables) are not followed.

usage: find_services.py [-o build/services.tsv] [--handles build/service_handles.tsv]
       [--detail] [--no-svc] [--code build/code_v130_dec.bin]
       [--exheader baserom/update_v130/exheader.bin] [--versions config/versions.json]
       [--functions config/functions.tsv] [--callgraph build/export/callgraph.tsv]
(about a minute on the full game; inputs are the maintainer's own dump, outputs go
under build/ and must not be committed)

Main output (TSV, one row per IPC request site and per svc site):
  function  mode  service  header  command  callers  site  handle  note
  service   the session's service ("svc" for system-call rows). It ends in '?' when the
            handle could not be traced and the service was inferred: from neighbouring
            request functions of one service (SDK client code for a service sits
            together), or from a header that only one known service uses.
  header    command header (svc number for svc rows); command: name from the tables,
            '~' = same command id but different parameter counts than the table
  callers   direct callers of the function (Ghidra call graph, or scanned)
  handle    where the session handle is read from, after resolving arguments through
            callers: 0x... a global, [0x...]+k a field of an object a global points to
--handles: every global that receives a session handle, an svc output (event, thread,
memory block...) or an IPC response word, and which functions store it.
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import struct
import sys
from collections import defaultdict
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

M32 = 0xFFFFFFFF
AL = 14
TMP = 16                      # scratch register slot used by multi-register transfers
CMDBUF = 0x80                 # thread command buffer offset inside the TLS area
SRV_GET_SERVICE_HANDLE = 0x00050100
SVC_SEND_SYNC_REQUEST = 0x32
SVC_CONNECT_TO_PORT = 0x2D

# ════════════════════════════════════════════════════════════════════════════════════
# Public knowledge: system calls and service command headers (3dbrew.org)
# ════════════════════════════════════════════════════════════════════════════════════

SVC_NAMES = {
    0x01: "ControlMemory", 0x02: "QueryMemory", 0x03: "ExitProcess",
    0x04: "GetProcessAffinityMask", 0x05: "SetProcessAffinityMask",
    0x06: "GetProcessIdealProcessor", 0x07: "SetProcessIdealProcessor",
    0x08: "CreateThread", 0x09: "ExitThread", 0x0A: "SleepThread",
    0x0B: "GetThreadPriority", 0x0C: "SetThreadPriority",
    0x0D: "GetThreadAffinityMask", 0x0E: "SetThreadAffinityMask",
    0x0F: "GetThreadIdealProcessor", 0x10: "SetThreadIdealProcessor",
    0x11: "GetCurrentProcessorNumber", 0x12: "Run",
    0x13: "CreateMutex", 0x14: "ReleaseMutex", 0x15: "CreateSemaphore", 0x16: "ReleaseSemaphore",
    0x17: "CreateEvent", 0x18: "SignalEvent", 0x19: "ClearEvent",
    0x1A: "CreateTimer", 0x1B: "SetTimer", 0x1C: "CancelTimer", 0x1D: "ClearTimer",
    0x1E: "CreateMemoryBlock", 0x1F: "MapMemoryBlock", 0x20: "UnmapMemoryBlock",
    0x21: "CreateAddressArbiter", 0x22: "ArbitrateAddress", 0x23: "CloseHandle",
    0x24: "WaitSynchronization1", 0x25: "WaitSynchronizationN", 0x26: "SignalAndWait",
    0x27: "DuplicateHandle", 0x28: "GetSystemTick", 0x29: "GetHandleInfo",
    0x2A: "GetSystemInfo", 0x2B: "GetProcessInfo", 0x2C: "GetThreadInfo",
    0x2D: "ConnectToPort", 0x2E: "SendSyncRequest1", 0x2F: "SendSyncRequest2",
    0x30: "SendSyncRequest3", 0x31: "SendSyncRequest4", 0x32: "SendSyncRequest",
    0x33: "OpenProcess", 0x34: "OpenThread", 0x35: "GetProcessId",
    0x36: "GetProcessIdOfThread", 0x37: "GetThreadId", 0x38: "GetResourceLimit",
    0x39: "GetResourceLimitLimitValues", 0x3A: "GetResourceLimitCurrentValues",
    0x3B: "GetThreadContext", 0x3C: "Break", 0x3D: "OutputDebugString",
    0x3E: "ControlPerformanceCounter",
    0x47: "CreatePort", 0x48: "CreateSessionToPort", 0x49: "CreateSession",
    0x4A: "AcceptSession", 0x4F: "ReplyAndReceive",
    0x50: "BindInterrupt", 0x51: "UnbindInterrupt", 0x52: "InvalidateProcessDataCache",
    0x53: "StoreProcessDataCache", 0x54: "FlushProcessDataCache",
    0x55: "StartInterProcessDma", 0x56: "StopDma", 0x57: "GetDmaState", 0x58: "RestartDma",
    0x59: "SetGpuProt", 0x5A: "SetWifiEnabled",
    0x70: "ControlProcessMemory", 0x71: "MapProcessMemory", 0x72: "UnmapProcessMemory",
    0x7C: "KernelSetState", 0x7D: "QueryProcessMemory", 0xFF: "StopPoint",
}

# System calls whose r1 output is a new handle (or address) worth following.
SVC_R1_OUTPUT = {0x01, 0x08, 0x13, 0x15, 0x17, 0x1A, 0x1E, 0x21, 0x27, 0x2D, 0x33, 0x34, 0x38}


def _t(text: str) -> dict[int, str]:
    out = {}
    for line in text.strip().splitlines():
        h, name = line.split()
        out[int(h, 16)] = name
    return out


# Full command headers: (command id << 16) | (normal words << 6) | translate words.
SERVICE_COMMANDS: dict[str, dict[int, str]] = {
    "srv:": _t("""
        00010002 RegisterClient
        00020000 EnableNotification
        00030100 RegisterService
        000400C0 UnregisterService
        00050100 GetServiceHandle
        000600C2 RegisterPort
        000700C0 UnregisterPort
        00080100 GetPort
        00090040 Subscribe
        000A0040 Unsubscribe
        000B0000 ReceiveNotification
        000C0080 PublishToSubscriber
        000D0040 PublishAndGetSubscriber
        000E00C0 IsServiceRegistered"""),
    "gsp::Gpu": _t("""
        00010082 WriteHWRegs
        00020084 WriteHWRegsWithMask
        00030082 WriteHWRegRepeat
        00040080 ReadHWRegs
        00050200 SetBufferSwap
        00060082 SetCommandList
        000700C2 RequestDma
        00080082 FlushDataCache
        00090082 InvalidateDataCache
        000A0044 RegisterInterruptEvents
        000B0040 SetLcdForceBlack
        000C0000 TriggerCmdReqQueue
        000D0140 SetDisplayTransfer
        000E0180 SetTextureCopy
        000F0200 SetMemoryFill
        00100040 SetAxiConfigQoSMode
        00110040 SetPerfLogMode
        00120000 GetPerfLog
        00130042 RegisterInterruptRelayQueue
        00140000 UnregisterInterruptRelayQueue
        00150002 TryAcquireRight
        00160042 AcquireRight
        00170000 ReleaseRight
        00180000 ImportDisplayCaptureInfo
        00190000 SaveVramSysArea
        001A0000 RestoreVramSysArea
        001B0000 ResetGpuCore
        001C0040 SetLedForceOff
        001D0040 SetTestCommand
        001E0080 SetInternalPriorities
        001F0082 StoreDataCache"""),
    "gsp::Lcd": _t("""
        000A0080 SetBrightnessRaw
        000B0080 SetBrightness
        000F0000 PowerOnAllBacklights
        00100000 PowerOffAllBacklights
        00110040 PowerOnBacklight
        00120040 PowerOffBacklight
        00130040 SetLedForceOff
        00140000 GetVendor
        00150040 GetBrightness"""),
    "hid:USER": _t("""
        00010000 CalibrateTouchScreen
        00020000 UpdateTouchConfig
        000A0000 GetIPCHandles
        000B0000 StartAnalogStickCalibration
        000E0000 GetAnalogStickCalibrateParam
        00110000 EnableAccelerometer
        00120000 DisableAccelerometer
        00130000 EnableGyroscopeLow
        00140000 DisableGyroscopeLow
        00150000 GetGyroscopeLowRawToDpsCoefficient
        00160000 GetGyroscopeLowCalibrateParam
        00170000 GetSoundVolume"""),
    "fs:USER": _t("""
        000100C6 Dummy1
        040100C4 Control
        08010002 Initialize
        080201C2 OpenFile
        08030204 OpenFileDirectly
        08040142 DeleteFile
        08050244 RenameFile
        08060142 DeleteDirectory
        08070142 DeleteDirectoryRecursively
        08080202 CreateFile
        08090182 CreateDirectory
        080A0244 RenameDirectory
        080B0102 OpenDirectory
        080C00C2 OpenArchive
        080D0144 ControlArchive
        080E0080 CloseArchive
        080F0180 FormatThisUserSaveData
        08100200 CreateLegacySystemSaveData
        08110040 DeleteLegacySystemSaveData
        08120080 GetFreeBytes
        08130000 GetCardType
        08140000 GetSdmcArchiveResource
        08150000 GetNandArchiveResource
        08160000 GetSdmcFatfsError
        08170000 IsSdmcDetected
        08180000 IsSdmcWritable
        08190042 GetSdmcCid
        081A0042 GetNandCid
        081B0000 GetSdmcSpeedInfo
        081C0000 GetNandSpeedInfo
        081D0042 GetSdmcLog
        081E0042 GetNandLog
        081F0000 ClearSdmcLog
        08200000 ClearNandLog
        08210000 CardSlotIsInserted
        08220000 CardSlotPowerOn
        08230000 CardSlotPowerOff
        08240000 CardSlotGetCardIFPowerStatus
        082E0040 GetProductInfo
        082F0040 GetProgramLaunchInfo
        08300182 CreateExtSaveDataLegacy
        08310180 CreateSharedExtSaveData
        08320102 ReadExtSaveDataIconLegacy
        08330082 EnumerateExtSaveDataLegacy
        08340082 EnumerateSharedExtSaveData
        08350080 DeleteExtSaveDataLegacy
        08360080 DeleteSharedExtSaveData
        08390000 SendInitializeInfoTo9
        083A0100 GetSpecialContentIndex
        083B00C2 GetLegacyRomHeader
        083C00C2 GetLegacyBannerData
        083D0100 CheckAuthorityToAccessExtSaveData
        083E00C2 QueryTotalQuotaSize
        08400040 AbnegateAccessRight
        084500C2 GetFormatInfo
        08490040 GetArchiveResource
        084C0242 FormatSaveData
        084D0102 GetLegacySubBannerData
        084F0102 ReadSpecialFile
        08500040 GetSpecialFileSize
        08510242 CreateExtSaveData
        08520100 DeleteExtSaveData
        08530142 ReadExtSaveDataIcon
        085400C0 GetExtDataBlockSize
        08550102 EnumerateExtSaveData
        08560240 CreateSystemSaveData
        08570080 DeleteSystemSaveData
        085A00C0 SetArchivePriority
        085B0080 GetArchivePriority
        08610042 InitializeWithSdkVersion
        08620040 SetPriority
        08630000 GetPriority
        08640000 GetNandInfo
        08650140 SetSaveDataSecureValue
        086600C0 GetSaveDataSecureValue
        086700C4 ControlSecureSave
        08680000 GetMediaType"""),
    # Sessions returned by fs:USER OpenFile / OpenFileDirectly / OpenDirectory.
    "fs:file": _t("""
        08010100 OpenSubFile
        080200C2 Read
        08030102 Write
        08040000 GetSize
        08050080 SetSize
        08060000 GetAttributes
        08070040 SetAttributes
        08080000 Close
        08090000 Flush
        080A0040 SetPriority
        080B0000 GetPriority
        080C0000 OpenLinkFile"""),
    "fs:dir": _t("""
        08010042 Read
        08020000 Close
        08030040 SetPriority
        08040000 GetPriority"""),
    "APT:U": _t("""
        00010040 GetLockHandle
        00020080 Initialize
        00030040 Enable
        00040040 Finalize
        00050040 GetAppletManInfo
        00060040 GetAppletInfo
        00070000 GetLastSignaledAppletId
        00080000 CountRegisteredApplet
        00090040 IsRegistered
        000A0040 GetAttribute
        000B0040 InquireNotification
        000C0104 SendParameter
        000D0080 ReceiveParameter
        000E0080 GlanceParameter
        000F0100 CancelParameter
        001000C2 DebugFunc
        001100C0 MapProgramIdForDebug
        00120040 SetHomeMenuAppletIdForDebug
        00130000 GetPreparationState
        00140040 SetPreparationState
        00150140 PrepareToStartApplication
        00160040 PreloadLibraryApplet
        00170040 FinishPreloadingLibraryApplet
        00180040 PrepareToStartLibraryApplet
        00190040 PrepareToStartSystemApplet
        001A0000 PrepareToStartNewestHomeMenu
        001B00C4 StartApplication
        001C0000 WakeupApplication
        001D0000 CancelApplication
        001E0084 StartLibraryApplet
        001F0084 StartSystemApplet
        00200044 StartNewestHomeMenu
        00210000 OrderToCloseApplication
        00220040 PrepareToCloseApplication
        00230040 PrepareToJumpToApplication
        00240044 JumpToApplication
        002500C0 PrepareToCloseLibraryApplet
        00260000 PrepareToCloseSystemApplet
        00270044 CloseApplication
        00280044 CloseLibraryApplet
        00290044 CloseSystemApplet
        002A0000 OrderToCloseSystemApplet
        002B0000 PrepareToJumpToHomeMenu
        002C0044 JumpToHomeMenu
        002D0000 PrepareToLeaveHomeMenu
        002E0044 LeaveHomeMenu
        002F0040 PrepareToLeaveResidentApplet
        00300044 LeaveResidentApplet
        00310100 PrepareToDoApplicationJump
        00320084 DoApplicationJump
        00330000 GetProgramIdOnApplicationJump
        00340084 SendDeliverArg
        00350080 ReceiveDeliverArg
        00360040 LoadSysMenuArg
        00370042 StoreSysMenuArg
        00380040 PreloadResidentApplet
        00390040 PrepareToStartResidentApplet
        003A0044 StartResidentApplet
        003B0040 CancelLibraryApplet
        003C0042 SendDspSleep
        003D0042 SendDspWakeUp
        003E0080 ReplySleepQuery
        003F0040 ReplySleepNotificationComplete
        00400042 SendCaptureBufferInfo
        00410040 ReceiveCaptureBufferInfo
        00420080 SleepSystem
        00430040 NotifyToWait
        00440000 GetSharedFont
        00450040 GetWirelessRebootInfo
        00460104 Wrap
        00470104 Unwrap
        00480100 GetProgramInfo
        00490180 Reboot
        004A0040 GetCaptureInfo
        004B00C2 AppletUtility
        004C0000 SetFatalErrDispMode
        004D0080 GetAppletProgramInfo
        004E0000 HardwareResetAsync
        004F0080 SetAppCpuTimeLimit
        00500040 GetAppCpuTimeLimit
        00510080 GetStartupArgument
        00520104 Wrap1
        00530104 Unwrap1
        00550040 SetScreenCapPostPermission
        00560000 GetScreenCapPostPermission
        00570044 WakeupApplication2
        00580002 GetProgramID
        01010000 GetTargetPlatform
        01020000 CheckNew3DS
        01030000 GetApplicationRunningMode
        01040000 IsStandardMemoryLayout"""),
    "cfg:u": _t("""
        00010082 GetConfigInfoBlk2
        00020000 SecureInfoGetRegion
        00030040 GenHashConsoleUnique
        00040000 GetRegionCanadaUSA
        00050000 GetSystemModel
        00060000 GetModelNintendo2DS
        00070040 WriteToFirstByteCfgSavegame
        00080080 GoThroughTable
        00090040 GetCountryCodeString
        000A0040 GetCountryCodeID
        000B0000 IsFangateSupported"""),
    "ptm:u": _t("""
        00010002 RegisterAlarmClient
        00020080 SetRtcAlarm
        00030000 GetRtcAlarm
        00040000 CancelRtcAlarm
        00050000 GetAdapterState
        00060000 GetShellState
        00070000 GetBatteryLevel
        00080000 GetBatteryChargeState
        00090000 GetPedometerState
        000A0042 GetStepHistoryEntry
        000B00C2 GetStepHistory
        000C0000 GetTotalStepCount
        000D0040 SetPedometerRecordingMode
        000E0000 GetPedometerRecordingMode
        000F0084 GetStepHistoryAll"""),
    "ac:u": _t("""
        00010000 CreateDefaultConfig
        00040006 ConnectAsync
        00050002 GetConnectResult
        00070002 CancelConnectAsync
        00080004 CloseAsync
        00090002 GetCloseResult
        000A0000 GetLastErrorCode
        000C0000 GetStatus
        000D0000 GetWifiStatus
        000E0042 GetCurrentAPInfo
        00100042 GetCurrentNZoneInfo
        00110042 GetNZoneApNumService
        001D0042 ScanAPs
        00240042 AddDenyApType
        00270002 GetInfraPriority
        002D0082 SetRequestEulaVersion
        00300004 RegisterDisconnectEvent
        003C0042 GetAPSSIDList
        003E0042 IsConnected
        00400042 SetClientVersion"""),
    "frd:u": _t("""
        00010000 HasLoggedIn
        00020000 IsOnline
        00030002 Login
        00040000 Logout
        00050000 GetMyFriendKey
        00060000 GetMyPreference
        00070000 GetMyProfile
        00080000 GetMyPresence
        00090000 GetMyScreenName
        000A0000 GetMyMii
        000B0000 GetMyLocalAccountId
        000C0000 GetMyPlayingGame
        000D0000 GetMyFavoriteGame
        000E0000 GetMyNcPrincipalId
        000F0000 GetMyComment
        00100040 GetMyPassword
        00110080 GetFriendKeyList
        00120042 GetFriendPresence
        00130142 GetFriendScreenName
        00140044 GetFriendMii
        00150042 GetFriendProfile
        00160042 GetFriendRelationship
        00170042 GetFriendAttributeFlags
        00180044 GetFriendPlayingGame
        00190042 GetFriendFavoriteGame
        001A00C4 GetFriendInfo
        001B0080 IsIncludedInFriendList
        001C0042 UnscrambleLocalFriendCode
        001D0002 UpdateGameModeDescription
        001E02C2 UpdateGameMode
        001F0042 SendInvitation
        00200002 AttachToEventNotification
        00210040 SetNotificationMask
        00220040 GetEventNotification
        00230000 GetLastResponseResult
        00240040 PrincipalIdToFriendCode
        00250080 FriendCodeToPrincipalId
        00260080 IsValidFriendCode
        00270040 ResultToErrorCode
        00280244 RequestGameAuthentication
        00290000 GetGameAuthenticationData
        002A0204 RequestServiceLocator
        002B0000 GetServiceLocatorData
        002C0002 DetectNatProperties
        002D0000 GetNatProperties
        002E0000 GetServerTimeInterval
        002F0040 AllowHalfAwake
        00300000 GetServerTypes
        00310082 GetFriendComment
        00320042 SetClientSdkVersion
        00330000 GetMyApproachContext
        00340046 AddFriendWithApproach
        00350082 DecryptApproachContext"""),
    "nwm::UDS": _t("""
        00010442 InitializeDeprecated
        00020000 Scrap
        00030000 Shutdown
        00040402 BeginHostingNetworkDeprecated
        00050040 EjectClient
        00060000 EjectSpectator
        00070080 UpdateNetworkAttribute
        00080000 DestroyNetwork
        00090442 ConnectNetworkDeprecated
        000A0000 DisconnectNetwork
        000B0000 GetConnectionStatus
        000D0040 GetNodeInformation
        000E0006 DecryptBeaconDataDeprecated
        000F0404 RecvBeaconBroadcastData
        00100042 SetApplicationData
        00110040 GetApplicationData
        00120100 Bind
        00130040 Unbind
        001400C0 PullPacket
        00150080 SetMaxSendDelay
        00170182 SendTo
        001A0000 GetChannel
        001B0302 InitializeWithVersion
        001D0044 BeginHostingNetwork
        001E0084 ConnectNetwork
        001F0006 DecryptBeaconData
        00200040 Flush
        00210080 SetProbeResponseParam
        00220402 ScanOnConnection"""),
    "dsp::DSP": _t("""
        00010040 RecvData
        00020040 RecvDataIsReady
        00030080 SendData
        00040040 SendDataIsEmpty
        000500C2 SendFifoEx
        000600C0 RecvFifoEx
        00070040 SetSemaphore
        00080000 GetSemaphore
        00090040 ClearSemaphore
        000A0040 MaskSemaphore
        000B0000 CheckSemaphoreRequest
        000C0040 ConvertProcessAddressFromDspDram
        000D0082 WriteProcessPipe
        000E00C0 ReadPipe
        000F0080 GetPipeReadableSize
        001000C0 ReadPipeIfPossible
        001100C2 LoadComponent
        00120000 UnloadComponent
        00130082 FlushDataCache
        00140082 InvalidateDataCache
        00150082 RegisterInterruptEvents
        00160000 GetSemaphoreEventHandle
        00170040 SetSemaphoreMask
        00180040 GetPhysicalAddress
        00190040 GetVirtualAddress
        001A0042 SetIirFilterI2S1_cmd1
        001B0042 SetIirFilterI2S1_cmd2
        001C0082 SetIirFilterEQ
        001D00C0 ReadMultiEx_SPI2
        001E00C2 WriteMultiEx_SPI2
        001F0000 GetHeadphoneStatus
        00200040 ForceHeadphoneOut
        00210000 GetIsDspOccupied"""),
    "ndm:u": _t("""
        00010042 EnterExclusiveState
        00020002 LeaveExclusiveState
        00030000 QueryExclusiveMode
        00040002 LockState
        00050002 UnlockState
        00060040 SuspendDaemons
        00070040 ResumeDaemons
        00080040 SuspendScheduler
        00090000 ResumeScheduler
        000A0000 GetCurrentState
        000B0000 GetTargetState
        000D0040 QueryStatus
        000E0040 GetDaemonDisableCount
        000F0000 GetSchedulerDisableCount
        00100040 SetScanInterval
        00110000 GetScanInterval
        00120040 SetRetryInterval
        00130000 GetRetryInterval
        00140040 OverrideDefaultDaemons
        00150000 ResetDefaultDaemons
        00160000 GetDefaultDaemons
        00170000 ClearHalfAwakeMacFilter"""),
    "ir:USER": _t("""
        00010182 InitializeIrNop
        00020000 FinalizeIrNop
        00030000 ClearReceiveBuffer
        00040000 ClearSendBuffer
        000500C0 WaitConnection
        00060040 RequireConnection
        000702C0 AutoConnection
        00080000 AnyConnection
        00090000 Disconnect
        000A0000 GetReceiveEvent
        000B0000 GetSendEvent
        000C0000 GetConnectionStatusEvent
        000D0042 SendIrNop
        000E0042 SendIrNopLarge
        000F0040 ReceiveIrnop
        00100042 ReceiveIrnopLarge
        00110040 GetLatestReceiveErrorResult
        00120040 GetLatestSendErrorResult
        00130000 GetConnectionStatus
        00140000 GetTryingToConnectStatus
        00150000 GetReceiveSizeFreeAndUsed
        00160000 GetSendSizeFreeAndUsed
        00170000 GetConnectionRole
        00180182 InitializeIrNopShared
        00190040 ReleaseReceivedData
        001A0040 SetOwnMachineId"""),
    "cam:u": _t("""
        00010040 StartCapture
        00020040 StopCapture
        00030040 IsBusy
        00040040 ClearBuffer
        00050040 GetVsyncInterruptEvent
        00060040 GetBufferErrorInterruptEvent
        00070102 SetReceiving
        00080040 IsFinishedReceiving
        00090100 SetTransferLines
        000A0080 GetMaxLines
        000B0100 SetTransferBytes
        000C0040 GetTransferBytes
        000D0080 GetMaxBytes
        000E0080 SetTrimming
        000F0040 IsTrimming
        00100140 SetTrimmingParams
        00110040 GetTrimmingParams
        00120140 SetTrimmingParamsCenter
        00130040 Activate
        00140080 SwitchContext
        00150080 SetExposure
        00160080 SetWhiteBalance
        00170080 SetWhiteBalanceWithoutBaseUp
        00180080 SetSharpness
        00190080 SetAutoExposure
        001A0040 IsAutoExposure
        001B0080 SetAutoWhiteBalance
        001C0040 IsAutoWhiteBalance
        001D00C0 FlipImage
        001E0200 SetDetailSize
        001F00C0 SetSize
        00200080 SetFrameRate
        00210080 SetPhotoMode
        002200C0 SetEffect
        00230080 SetContrast
        00240080 SetLensCorrection
        002500C0 SetOutputFormat
        00260140 SetAutoExposureWindow
        00270140 SetAutoWhiteBalanceWindow
        00280080 SetNoiseFilter
        00290080 SynchronizeVsyncTiming
        002A0080 GetLatestVsyncTiming
        002B0000 GetStereoCameraCalibrationData
        00360000 GetSuitableY2rStandardCoefficient
        00380040 PlayShutterSound
        00390000 DriverInitialize
        003A0000 DriverFinalize
        003B0000 GetActivatedCamera
        003C0000 GetSleepCamera
        003D0040 SetSleepCamera
        003E0040 SetBrightnessSynchronization"""),
    "y2r:u": _t("""
        00010040 SetInputFormat
        00020000 GetInputFormat
        00030040 SetOutputFormat
        00040000 GetOutputFormat
        00050040 SetRotation
        00060000 GetRotation
        00070040 SetBlockAlignment
        00080000 GetBlockAlignment
        00090040 SetSpacialDithering
        000A0000 GetSpacialDithering
        000B0040 SetTemporalDithering
        000C0000 GetTemporalDithering
        000D0040 SetTransferEndInterrupt
        000E0000 GetTransferEndInterrupt
        000F0000 GetTransferEndEvent
        00100102 SetSendingY
        00110102 SetSendingU
        00120102 SetSendingV
        00130102 SetSendingYUYV
        00140000 IsFinishedSendingYuv
        00150000 IsFinishedSendingY
        00160000 IsFinishedSendingU
        00170000 IsFinishedSendingV
        00180102 SetReceiving
        00190000 IsFinishedReceiving
        001A0040 SetInputLineWidth
        001B0000 GetInputLineWidth
        001C0040 SetInputLines
        001D0000 GetInputLines
        001E0100 SetCoefficient
        001F0000 GetCoefficient
        00200040 SetStandardCoefficient
        00210040 GetStandardCoefficientParams
        00220040 SetAlpha
        00230000 GetAlpha
        00240200 SetDitheringWeightParams
        00250000 GetDitheringWeightParams
        00260000 StartConversion
        00270000 StopConversion
        00280000 IsBusyConversion
        002901C0 SetPackageParameter
        002A0000 PingProcess
        002B0000 DriverInitialize
        002C0000 DriverFinalize
        002D0000 GetPackageParameter"""),
    "am:app": _t("""
        100100C0 GetDLCContentInfoCount
        10020104 FindDLCContentInfos
        10030142 ListDLCContentInfos
        10040102 DeleteContents
        10050084 GetDLCTitleInfos
        10060080 GetNumDataTitleTickets
        10070102 ListDataTitleTicketInfos
        100801C2 GetItemRights
        100900C0 IsDataTitleInUse
        100A0000 IsExternalTitleDatabaseInitialized"""),
    "act:u": _t("""
        00010084 Initialize
        00020040 GetErrorCode
        000600C2 GetAccountDataBlock"""),
    "cecd:u": _t("""
        000100C2 Open
        00020042 Read
        00030104 ReadMessage
        00040106 ReadMessageWithHMAC
        00050042 Write
        00060104 WriteMessage
        00070106 WriteMessageWithHMAC
        00080102 Delete
        000900C2 SetData
        000A00C4 ReadData
        000B0040 Start
        000C0040 Stop
        000D0082 GetCecInfoBuffer
        000E0000 GetCecdState
        000F0000 GetCecInfoEventHandle
        00100000 GetChangeStateEventHandle
        00110104 OpenAndWrite
        00120104 OpenAndRead"""),
    "soc:U": _t("""
        00010044 InitializeSockets
        000200C2 socket
        00030082 listen
        00040082 accept
        00050084 bind
        00060084 connect
        00070104 recvfrom_other
        00080102 recvfrom
        00090106 sendto_other
        000A0106 sendto
        000B0042 close
        000C0082 shutdown
        000D0082 gethostbyname
        000E00C2 gethostbyaddr
        000F0106 getaddrinfo
        00100102 getnameinfo
        00110102 getsockopt
        00120104 setsockopt
        001300C2 fcntl
        00140084 poll
        00150042 sockatmark
        00160000 gethostid
        00170082 getsockname
        00180082 getpeername
        00190000 ShutdownSockets
        001A00C0 GetNetworkOpt
        001B0040 ICMPSocket
        001C0104 ICMPPing
        001D0040 ICMPCancel
        001E0040 ICMPClose
        001F0040 GetResolverInfo
        00210002 CloseSockets"""),
    "http:C": _t("""
        00010044 Initialize
        00020082 CreateContext
        00030040 CloseContext
        00040040 CancelConnection
        00050040 GetRequestState
        00060040 GetDownloadSizeState
        00070040 GetRequestError
        00080042 InitializeConnectionSession
        00090040 BeginRequest
        000A0040 BeginRequestAsync
        000B0082 ReceiveData
        000C0102 ReceiveDataTimeout
        000D0146 SetProxy
        000E0040 SetProxyDefault
        000F00C4 SetBasicAuthorization
        00100080 SetSocketBufferSize
        001100C4 AddRequestHeader
        001200C4 AddPostDataAscii
        001300C4 AddPostDataBinary
        00140082 AddPostDataRaw
        00150080 SetPostDataType
        001600C4 SendPostDataAscii
        00170144 SendPostDataAsciiTimeout
        001800C4 SendPostDataBinary
        00190144 SendPostDataBinaryTimeout
        001A0082 SendPostDataRaw
        001B0102 SendPostDataRawTimeout
        001C0080 SetPostDataEncoding
        001D0040 NotifyFinishSendPostData
        001E00C4 GetResponseHeader
        001F0144 GetResponseHeaderTimeout
        00200082 GetResponseData
        00210102 GetResponseDataTimeout
        00220040 GetResponseStatusCode
        002300C0 GetResponseStatusCodeTimeout
        00240082 AddTrustedRootCA
        00250080 AddDefaultCert
        00260080 SelectRootCertChain
        002700C4 SetClientCert
        00280080 SetClientCertDefault
        00290080 SetClientCertContext
        002A0040 GetSSLError
        002B0080 SetSSLOpt
        002C0080 SetSSLClearOpt
        002D0000 CreateRootCertChain
        002E0040 DestroyRootCertChain
        002F0082 RootCertChainAddCert
        00300080 RootCertChainAddDefaultCert
        00310080 RootCertChainRemoveCert
        00320084 OpenClientCertContext
        00330040 OpenDefaultClientCertContext
        00340040 CloseClientCertContext
        00350186 SetDefaultProxy
        00360000 ClearDNSCache
        00370080 SetKeepAlive
        003800C0 SetPostDataTypeSize
        00390000 Finalize"""),
    "boss:U": _t("""
        00010082 InitializeSession
        00020100 SetStorageInfo
        00030000 UnregisterStorage
        00040000 GetStorageInfo
        00050042 RegisterPrivateRootCa
        00060084 RegisterPrivateClientCert
        00070000 GetNewArrivalFlag
        00080002 RegisterNewArrivalEvent
        00090040 SetOptoutFlag
        000A0000 GetOptoutFlag
        000B00C2 RegisterTask
        000C0082 UnregisterTask
        000D0082 ReconfigureTask
        000E0000 GetTaskIdList
        000F0042 GetStepIdList
        00100102 GetNsDataIdList
        00110102 GetOwnNsDataIdList
        00120102 GetNewDataNsDataIdList
        00130102 GetOwnNewDataNsDataIdList
        00140082 SendProperty
        00150042 SendPropertyHandle
        00160082 ReceiveProperty
        00170082 UpdateTaskInterval
        00180082 UpdateTaskCount
        00190042 GetTaskInterval
        001A0042 GetTaskCount
        001B0042 GetTaskServiceStatus
        001C0042 StartTask
        001D0042 StartTaskImmediate
        001E0042 CancelTask
        001F0000 GetTaskFinishHandle
        00200082 GetTaskState
        00210042 GetTaskResult
        00220042 GetTaskCommErrorCode
        002300C2 GetTaskStatus
        00240082 GetTaskError
        00250082 GetTaskInfo
        00260040 DeleteNsData
        002700C2 GetNsDataHeaderInfo
        00280102 ReadNsData
        00290080 SetNsDataAdditionalInfo
        002A0040 GetNsDataAdditionalInfo
        002B0080 SetNsDataNewFlag
        002C0040 GetNsDataNewFlag
        002D0040 GetNsDataLastUpdate
        002E0040 GetErrorCode
        002F0140 RegisterStorageEntry
        00300000 GetStorageEntryInfo
        00310100 SetStorageOption
        00320000 GetStorageOption
        00330042 StartBgImmediate
        00340082 GetTaskProperty0
        003500C2 RegisterImmediateTask
        00360084 SetTaskQuery
        00370084 GetTaskQuery
        04160142 GetNsDataHeaderInfoPrivileged
        04170182 ReadNsDataPrivileged"""),
    "err:f": _t("""
        00010800 Throw
        00020042 SetUserString"""),
}
# Other names of the same interface (the SDK tries several, e.g. APT:S, APT:A, APT:U).
SERVICE_ALIASES = {
    "hid:SPVR": "hid:USER", "APT:A": "APT:U", "APT:S": "APT:U", "apt:U": "APT:U",
    "fs:LDR": "fs:USER", "ptm:sysm": "ptm:u", "ptm:s": "ptm:u", "ptm:gets": "ptm:u",
    "cfg:s": "cfg:u", "cfg:i": "cfg:u", "cecd:s": "cecd:u", "ac:i": "ac:u", "frd:a": "frd:u",
    "y2r:i": "y2r:u", "cam:s": "cam:u", "ir:u": "ir:USER", "boss:P": "boss:U", "boss:M": "boss:U",
}


def commands_of(service: str) -> dict[int, str]:
    return SERVICE_COMMANDS.get(SERVICE_ALIASES.get(service, service), {})


# Responses that are themselves new sessions: (header, response offset) -> service.
SESSION_FROM_RESPONSE = {
    (0x080201C2, 0x0C): "fs:file", (0x08030204, 0x0C): "fs:file",
    (0x08010100, 0x0C): "fs:file", (0x080B0102, 0x0C): "fs:dir",
}


def command_name(service: str | None, header: int | None) -> tuple[str, str]:
    """(command, note) for a header. Exact header match first, then by command id only
    (parameter counts differ from the table: marked with '~')."""
    if header is None:
        return "?", ""
    names = [s.rstrip("?") for s in service.split("|")] if service else []
    for s in names:
        table = commands_of(s)
        if header in table:
            return table[header], ""
    for s in names:
        for h, n in commands_of(s).items():
            if h >> 16 == header >> 16:
                return n + "~", f"table header 0x{h:08x}"
    return "?", ""


def guess_service(header: int | None) -> str | None:
    """The one service whose table contains this exact header, if it is unique."""
    if header is None:
        return None
    hits = [s for s, t in SERVICE_COMMANDS.items() if header in t]
    return hits[0] if len(hits) == 1 else None


# ════════════════════════════════════════════════════════════════════════════════════
# Program image
# ════════════════════════════════════════════════════════════════════════════════════

@dataclass
class Layout:
    base: int
    text: tuple[int, int]        # [start, end)
    rodata: tuple[int, int]
    data: tuple[int, int]
    bss: tuple[int, int]


class Image:
    def __init__(self, code: bytes, layout: Layout):
        self.code, self.layout, self.base = code, layout, layout.base

    def read32(self, a: int) -> int | None:
        off = a - self.base
        if a & 3 or off < 0 or off + 4 > len(self.code):
            return None
        return struct.unpack_from("<I", self.code, off)[0]

    def read16(self, a: int) -> int | None:
        off = a - self.base
        if a & 1 or off < 0 or off + 2 > len(self.code):
            return None
        return struct.unpack_from("<H", self.code, off)[0]

    def in_text(self, a: int) -> bool:
        return self.layout.text[0] <= a < self.layout.text[1]

    def read_only(self, a: int) -> bool:
        lo = self.layout
        return lo.text[0] <= a < lo.text[1] or lo.rodata[0] <= a < lo.rodata[1]

    def initialized(self, a: int) -> bool:
        lo = self.layout
        return lo.text[0] <= a < lo.data[1]

    def segment(self, a: int) -> str:
        lo = self.layout
        for name in ("text", "rodata", "data", "bss"):
            r = getattr(lo, name)
            if r[0] <= a < r[1]:
                return name
        return ""

    def cstring(self, a: int, limit: int = 64) -> str | None:
        off = a - self.base
        if off < 0 or off >= len(self.code):
            return None
        end = self.code.find(b"\0", off, off + limit)
        if end <= off:
            return None
        raw = self.code[off:end]
        if all(0x20 <= c < 0x7F for c in raw):
            return raw.decode()
        return None


def load_layout(exheader_path: str | None, versions_path: str | None) -> Layout | None:
    if exheader_path and os.path.exists(exheader_path):
        import exheader as exh
        with open(exheader_path, "rb") as f:
            h = exh.parse(f.read())
        ro = h.text.address + h.text.padded_size
        data = ro + h.ro.padded_size
        return Layout(h.text.address, (h.text.address, h.text.address + h.text.size),
                      (h.ro.address, h.ro.address + h.ro.size),
                      (h.data.address, h.data.address + h.data.size),
                      (h.data.address + h.data.size, h.data.address + h.data.size + h.bss_size))
    if versions_path and os.path.exists(versions_path):
        with open(versions_path, encoding="utf-8") as f:
            for v in json.load(f).get("versions", []):
                if v.get("target") and "segments" in v:
                    seg = {k: (int(a, 16), int(a, 16) + int(s, 16)) for k, (a, s) in v["segments"].items()}
                    return Layout(seg["text"][0], seg["text"], seg["rodata"], seg["data"], seg["bss"])
    return None


def load_image(code_path: str, layout: Layout, exheader_path: str | None = None) -> Image:
    with open(code_path, "rb") as f:
        raw = f.read()
    if len(raw) < layout.data[1] - layout.base and exheader_path and os.path.exists(exheader_path):
        import codebin2elf
        import exheader as exh
        with open(exheader_path, "rb") as f:
            raw = codebin2elf.load_code(exh.parse(f.read()), raw)
    return Image(raw, layout)


def read_tsv(path: str) -> list[dict[str, str]]:
    rows = []
    with open(path, encoding="utf-8", newline="") as f:
        header = None
        for line in f:
            line = line.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if header is None:
                header = parts
                continue
            rows.append(dict(zip(header, parts)))
    return rows


def load_functions(path: str) -> dict[int, tuple[int, str]]:
    return {int(r["address"], 16): (int(r["size"], 16), r["mode"]) for r in read_tsv(path)}


def load_callgraph(path: str) -> dict[int, set[int]]:
    callers: dict[int, set[int]] = defaultdict(set)
    for r in read_tsv(path):
        callers[int(r["callee"], 16)].add(int(r["caller"], 16))
    return callers


def scan_callgraph(img: Image, funcs: dict[int, tuple[int, str]]) -> dict[int, set[int]]:
    """Direct calls (BL/BLX, and B to a function entry) found by scanning function bodies.
    A fallback for when no Ghidra call graph is available; literal-pool words can add a
    few false edges, which later analysis ignores (call sites are re-decoded)."""
    callers: dict[int, set[int]] = defaultdict(set)
    for start, (size, mode) in funcs.items():
        if mode == "arm":
            for a in range(start, start + size - 3, 4):
                w = img.read32(a)
                if w is None:
                    continue
                cond, op = w >> 28, (w >> 24) & 0xF
                if op in (0xA, 0xB):
                    t = (a + 8 + (_sext(w & 0xFFFFFF, 24) << 2)) & M32
                    if cond == 0xF:
                        t |= ((w >> 24) & 1) << 1
                    elif op == 0xA and (t not in funcs or start <= t < start + size):
                        continue
                    if t in funcs or (t & ~1) in funcs:
                        callers[t & ~1].add(start)
        else:
            a = start
            while a < start + size - 3:
                hw, hw2 = img.read16(a), img.read16(a + 2)
                if hw is not None and hw2 is not None and hw >> 11 == 0x1E and hw2 >> 11 in (0x1F, 0x1D):
                    t = (a + 4 + ((_sext(hw & 0x7FF, 11) << 12) | ((hw2 & 0x7FF) << 1))) & M32
                    if hw2 >> 11 == 0x1D:
                        t &= ~3
                    if t in funcs:
                        callers[t].add(start)
                    a += 4
                else:
                    a += 2
    return callers


# ════════════════════════════════════════════════════════════════════════════════════
# Decoders: ARM (ARMv6K) and Thumb-1 into a tiny IR
# ════════════════════════════════════════════════════════════════════════════════════
#
# IR operations, executed in order:
#   ("set", rd, src)            rd = src
#   ("ld", rt, src, size)       rt = memory[src]
#   ("st", rt, src, size)       memory[src] = rt
#   ("kill", r)                 r = unknown
#   ("tls", rd)                 rd = thread-local storage base (mrc p15,0,rd,c13,c0,3)
#   ("svc", n)
# src expressions: ("k", const) ("r", reg) ("op", name, a, b) ("not", a) ("unk",)
# Control flow (Insn.flow): ("b", target, mode) ("bl", target, mode) ("ret",) ("bxr", r)
#   ("blxr", r) ("ijump",) ("jtab_add",) ("jtab_ldr",) ("trap",)

SHIFTS = ("lsl", "lsr", "asr", "ror")
DP_NAMES = ("and", "eor", "sub", "rsb", "add", "adc", "sbc", "rsc",
            "tst", "teq", "cmp", "cmn", "orr", "mov", "bic", "mvn")


@dataclass(slots=True)
class Insn:
    addr: int
    size: int
    mode: str
    cond: int = AL
    ops: tuple = ()
    flow: tuple | None = None
    sets_flags: bool = False


def _sext(v: int, bits: int) -> int:
    v &= (1 << bits) - 1
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def _ror(v: int, n: int) -> int:
    n &= 31
    return ((v >> n) | (v << (32 - n))) & M32 if n else v & M32


def _addr(base: tuple, off: tuple | None, up: bool = True) -> tuple:
    if off is None or off == ("k", 0):
        return base
    return ("op", "add" if up else "sub", base, off)


def _reg(r: int, pc: int) -> tuple:
    return ("k", pc & M32) if r == 15 else ("r", r)


def _arm_shifted(w: int, pc: int) -> tuple:
    rm = w & 0xF
    base = _reg(rm, pc)
    st = (w >> 5) & 3
    if w & 0x10:
        return ("op", SHIFTS[st], base, ("r", (w >> 8) & 0xF))
    n = (w >> 7) & 0x1F
    if n == 0:
        if st == 0:
            return base
        if st == 3:
            return ("unk",)                       # RRX
        n = 32
    return ("op", SHIFTS[st], base, ("k", n))


def _ldm_stm(load: bool, rn: int, regs: list[int], before: bool, up: bool, wb: bool) -> tuple[list, tuple | None]:
    n = len(regs)
    start = (4 if before else 0) if up else (-4 * n if before else -4 * n + 4)
    ops: list = [("set", TMP, _addr(("r", rn), ("k", start)) if start >= 0 else ("op", "sub", ("r", rn), ("k", -start)))]
    for k, r in enumerate(regs):
        a = _addr(("r", TMP), ("k", 4 * k))
        if load:
            if r != 15:
                ops.append(("ld", r, a, 4))
        else:
            ops.append(("st", r, a, 4))
    if wb and not (load and rn in regs):
        ops.append(("set", rn, ("op", "add" if up else "sub", ("r", rn), ("k", 4 * n))))
    flow = None
    if load and 15 in regs:
        flow = ("ret",) if rn == 13 else ("ijump",)
    return ops, flow


def decode_arm(w: int, addr: int) -> Insn:
    pc = addr + 8
    cond = w >> 28
    ins = Insn(addr, 4, "arm", AL if cond == 0xF else cond)
    if cond == 0xF:
        if (w >> 25) & 7 == 5:                                  # BLX imm
            t = (pc + (_sext(w & 0xFFFFFF, 24) << 2) + (((w >> 24) & 1) << 1)) & M32
            ins.flow = ("bl", t, "thumb")
        return ins                                              # PLD, CPS, SETEND, CLREX, ...
    op3 = (w >> 25) & 7
    rn, rd = (w >> 16) & 0xF, (w >> 12) & 0xF

    if op3 == 0 and (w & 0x90) == 0x90 and (w & 0x60) == 0:     # multiply / swap / exclusive
        ins.sets_flags = (w >> 23) & 0x1F in (0, 1) and bool((w >> 20) & 1)
        if (w >> 23) & 0x1F == 0:                               # MUL, MLA, UMAAL
            ins.ops = (("kill", rn),) if not (w >> 22) & 1 else (("kill", rn), ("kill", rd))
        elif (w >> 23) & 0x1F == 1:                             # long multiplies
            ins.ops = (("kill", rn), ("kill", rd))
        else:                                                   # SWP, LDREX, STREX
            ins.ops = (("kill", rd),)
        return ins
    if op3 == 0 and (w & 0x90) == 0x90:                         # extra load/store
        p, u, imm, wbit, load = (w >> 24) & 1, (w >> 23) & 1, (w >> 22) & 1, (w >> 21) & 1, (w >> 20) & 1
        sh = (w >> 5) & 3
        off = ("k", ((w >> 4) & 0xF0) | (w & 0xF)) if imm else ("r", w & 0xF)
        base = _reg(rn, pc)
        a = _addr(base, off, bool(u)) if p else base
        ops: list = []
        if sh == 1:
            ops.append(("ld" if load else "st", rd, a, 2))
        elif load:                                              # LDRSB / LDRSH
            ops.append(("ld", rd, a, 1 if sh == 2 else 2))
        elif sh == 2:                                           # LDRD
            ops += [("set", TMP, a), ("ld", rd, ("r", TMP), 4), ("ld", rd + 1, _addr(("r", TMP), ("k", 4)), 4)]
        else:                                                   # STRD
            ops += [("set", TMP, a), ("st", rd, ("r", TMP), 4), ("st", rd + 1, _addr(("r", TMP), ("k", 4)), 4)]
        loads_rn = (load or sh == 2) and rn in (rd, rd + 1 if sh == 2 and not load else rd)
        if (not p or wbit) and not loads_rn:
            ops.append(("set", rn, _addr(("r", rn), off, bool(u))))
        ins.ops = tuple(ops)
        return ins
    if op3 == 0 and (w >> 23) & 3 == 2 and not (w >> 20) & 1:  # misc: MRS, MSR, BX, BLX, CLZ, BKPT, QADD...
        if (w & 0x0FFFFFF0) == 0x012FFF10:
            ins.flow = ("ret",) if w & 0xF == 14 else ("bxr", w & 0xF)
        elif (w & 0x0FFFFFF0) == 0x012FFF30:
            ins.flow = ("blxr", w & 0xF)
        elif (w & 0x0FF000F0) == 0x01200070:
            ins.flow = ("trap",)
        elif (w >> 21) & 1 and (w & 0xF0) == 0:                 # MSR
            ins.sets_flags = True
        else:
            ins.ops = (("kill", rd), ("kill", rn))
        return ins
    if op3 in (0, 1):                                           # data processing
        if op3 == 1 and (w >> 23) & 3 == 2 and not (w >> 20) & 1:   # MSR imm / MOVW-style
            if not (w >> 21) & 1:
                ins.ops = (("kill", rd),)
            else:
                ins.sets_flags = True
            return ins
        opc = (w >> 21) & 0xF
        ins.sets_flags = bool((w >> 20) & 1)
        name = DP_NAMES[opc]
        src2 = ("k", _ror(w & 0xFF, ((w >> 8) & 0xF) * 2)) if op3 == 1 else _arm_shifted(w, pc)
        if 8 <= opc <= 11:
            return ins
        a = _reg(rn, pc)
        if name == "mov":
            expr = src2
        elif name == "mvn":
            expr = ("not", src2)
        elif name in ("adc", "sbc", "rsc"):
            expr = ("unk",)
        elif name == "rsb":
            expr = ("op", "sub", src2, a)
        else:
            expr = ("op", name, a, src2)
        if rd == 15:
            if expr == ("r", 14) or (name in ("sub", "mov") and rn == 14):
                ins.flow = ("ret",)
            elif name == "add" and rn == 15 and op3 == 0:
                ins.flow = ("jtab_add",)
            elif name == "mov" and src2[0] == "r":
                ins.flow = ("bxr", src2[1])
            else:
                ins.flow = ("ijump",)
        else:
            ins.ops = (("set", rd, expr),)
        return ins
    if op3 in (2, 3):                                           # load/store word/byte, media
        if op3 == 3 and w & 0x10:
            if (w & 0x0FF000F0) == 0x07F000F0:
                ins.flow = ("trap",)
            else:
                ins.ops = (("kill", rd), ("kill", rn))
            return ins
        p, u, b, wbit, load = (w >> 24) & 1, (w >> 23) & 1, (w >> 22) & 1, (w >> 21) & 1, (w >> 20) & 1
        off = _arm_shifted(w, pc) if op3 == 3 else ("k", w & 0xFFF)
        base = _reg(rn, pc)
        a = _addr(base, off, bool(u)) if p else base
        size = 1 if b else 4
        ops = []
        if load and rd == 15:
            if rn == 13 and not p and u:
                ins.flow = ("ret",)
            elif rn == 15 and p and op3 == 2:
                ins.flow = ("ldrpc_lit", a)
            elif rn == 15:
                ins.flow = ("jtab_ldr",)
            else:
                ins.flow = ("ijump",)
        elif load:
            ops.append(("ld", rd, a, size))
        else:
            ops.append(("st", rd, a, size) if rd != 15 else ("st", -1, a, size))
        if (not p or wbit) and not (load and rd == rn):
            ops.append(("set", rn, _addr(("r", rn), off, bool(u))))
        ins.ops = tuple(ops)
        return ins
    if op3 == 4:                                                # LDM/STM
        regs = [i for i in range(16) if w & (1 << i)]
        ops, flow = _ldm_stm(bool((w >> 20) & 1), rn, regs, bool((w >> 24) & 1), bool((w >> 23) & 1),
                             bool((w >> 21) & 1))
        ins.ops, ins.flow = tuple(ops), flow
        return ins
    if op3 == 5:                                                # B / BL
        t = (pc + (_sext(w & 0xFFFFFF, 24) << 2)) & M32
        ins.flow = ("bl" if (w >> 24) & 1 else "b", t, "arm")
        return ins
    if op3 == 6:                                                # coprocessor load/store, MCRR/MRRC
        if (w & 0x0FF00000) == 0x0C500000:
            ins.ops = (("kill", rd), ("kill", rn))
        elif (w & 0x0FE00000) != 0x0C400000:
            p, u, wbit, load = (w >> 24) & 1, (w >> 23) & 1, (w >> 21) & 1, (w >> 20) & 1
            n = (w & 0xFF) * 4
            ops = []
            if (w >> 9) & 7 == 5 and not load:                  # VSTR / VSTM: memory changes
                if p and not wbit:
                    a = _addr(_reg(rn, pc), ("k", n), bool(u))
                    words = 2 if (w >> 8) & 1 else 1
                else:
                    a = _reg(rn, pc) if u else ("op", "sub", _reg(rn, pc), ("k", n))
                    words = n // 4
                ops += [("st", -1, _addr(a, ("k", 4 * i)), 4) for i in range(min(words, 64))]
            if wbit:                                            # VPUSH/VPOP/VLDM!/VSTM!
                ops.append(("set", rn, ("op", "add" if u else "sub", ("r", rn), ("k", n))))
            ins.ops = tuple(ops)
        return ins
    # op3 == 7
    if (w >> 24) & 1:
        ins.ops = (("svc", w & 0xFFFFFF),)
    elif w & 0x10 and (w >> 20) & 1:                            # MRC
        if (w & 0x0FFF0FFF) == 0x0E1D0F70:
            ins.ops = (("tls", rd),)
        elif rd != 15:
            ins.ops = (("kill", rd),)
        else:
            ins.sets_flags = True                               # VMRS APSR_nzcv, FPSCR
    return ins


def decode_thumb(addr: int, read16) -> Insn:
    hw = read16(addr)
    ins = Insn(addr, 2, "thumb")
    if hw is None:
        ins.flow = ("trap",)
        return ins
    pc = addr + 4
    top5 = hw >> 11
    lo3, mid3 = hw & 7, (hw >> 3) & 7
    if top5 <= 2:                                               # LSL/LSR/ASR #imm
        n = (hw >> 6) & 31
        if top5 == 0 and n == 0:
            e = ("r", mid3)
        else:
            e = ("op", SHIFTS[top5], ("r", mid3), ("k", n or 32))
        ins.ops = (("set", lo3, e),)
    elif top5 == 3:                                             # ADD/SUB reg/imm3
        v = (hw >> 6) & 7
        src = ("k", v) if hw & 0x400 else ("r", v)
        ins.ops = (("set", lo3, ("op", "sub" if hw & 0x200 else "add", ("r", mid3), src)),)
    elif top5 <= 7:                                             # MOV/CMP/ADD/SUB #imm8
        rd, imm, op = (hw >> 8) & 7, hw & 0xFF, top5 - 4
        if op == 0:
            ins.ops = (("set", rd, ("k", imm)),)
        elif op in (2, 3):
            ins.ops = (("set", rd, ("op", "add" if op == 2 else "sub", ("r", rd), ("k", imm))),)
    elif hw >> 10 == 0x10:                                      # data processing (register)
        op = (hw >> 6) & 0xF
        a, b = ("r", lo3), ("r", mid3)
        table = {0: ("op", "and", a, b), 1: ("op", "eor", a, b), 2: ("op", "lsl", a, b),
                 3: ("op", "lsr", a, b), 4: ("op", "asr", a, b), 5: ("unk",), 6: ("unk",),
                 7: ("op", "ror", a, b), 9: ("op", "sub", ("k", 0), b), 12: ("op", "orr", a, b),
                 13: ("op", "mul", a, b), 14: ("op", "bic", a, b), 15: ("not", b)}
        if op in table:
            ins.ops = (("set", lo3, table[op]),)
    elif hw >> 10 == 0x11:                                      # hi-register ops, BX/BLX
        op, rm = (hw >> 8) & 3, (hw >> 3) & 0xF
        rd = lo3 | ((hw >> 4) & 8)
        src = _reg(rm, pc)
        if op == 0:
            if rd == 15:
                ins.flow = ("ijump",)
            else:
                ins.ops = (("set", rd, ("op", "add", _reg(rd, pc), src)),)
        elif op == 2:
            if rd == 15:
                ins.flow = ("ret",) if rm == 14 else ("bxr", rm)
            else:
                ins.ops = (("set", rd, src),)
        elif op == 3:
            if hw & 0x80:
                ins.flow = ("blxr", rm)
            else:
                ins.flow = ("ret",) if rm == 14 else ("bxr", rm)
    elif top5 == 9:                                             # LDR literal
        ins.ops = (("ld", (hw >> 8) & 7, ("k", (pc & ~3) + (hw & 0xFF) * 4), 4),)
    elif hw >> 12 == 5:                                         # load/store register offset
        opb = (hw >> 9) & 7
        a = ("op", "add", ("r", mid3), ("r", (hw >> 6) & 7))
        size = (4, 2, 1, 1, 4, 2, 1, 2)[opb]
        ins.ops = (("st" if opb < 3 else "ld", lo3, a, size),)
    elif hw >> 13 == 3 or hw >> 12 == 8:                        # LDR/STR(B/H) #imm5
        imm5 = (hw >> 6) & 31
        if hw >> 12 == 8:
            size, off = 2, imm5 * 2
        else:
            size = 1 if hw & 0x1000 else 4
            off = imm5 * size
        a = _addr(("r", mid3), ("k", off))
        ins.ops = (("ld" if hw & 0x800 else "st", lo3, a, size),)
    elif hw >> 12 == 9:                                         # LDR/STR sp-relative
        a = _addr(("r", 13), ("k", (hw & 0xFF) * 4))
        ins.ops = (("ld" if hw & 0x800 else "st", (hw >> 8) & 7, a, 4),)
    elif hw >> 12 == 0xA:                                       # ADR / ADD rd, sp, #imm
        rd, imm = (hw >> 8) & 7, (hw & 0xFF) * 4
        e = _addr(("r", 13), ("k", imm)) if hw & 0x800 else ("k", (pc & ~3) + imm)
        ins.ops = (("set", rd, e),)
    elif hw >> 12 == 0xB:                                       # misc
        if hw >> 8 == 0xB0:
            ins.ops = (("set", 13, ("op", "sub" if hw & 0x80 else "add", ("r", 13), ("k", (hw & 0x7F) * 4))),)
        elif (hw >> 9) & 3 == 2:                                # PUSH / POP
            regs = [i for i in range(8) if hw & (1 << i)]
            if hw & 0x800:
                regs += [15] if hw & 0x100 else []
                ops, flow = _ldm_stm(True, 13, regs, False, True, True)
            else:
                regs += [14] if hw & 0x100 else []
                ops, flow = _ldm_stm(False, 13, regs, True, False, True)
            ins.ops, ins.flow = tuple(ops), flow
        elif hw >> 8 in (0xB2, 0xBA):                           # extend, REV
            ins.ops = (("kill", lo3),)
        elif hw >> 8 == 0xBE:
            ins.flow = ("trap",)
    elif hw >> 12 == 0xC:                                       # LDMIA/STMIA rn!
        rn = (hw >> 8) & 7
        regs = [i for i in range(8) if hw & (1 << i)]
        ops, flow = _ldm_stm(bool(hw & 0x800), rn, regs, False, True, True)
        ins.ops, ins.flow = tuple(ops), flow
    elif hw >> 12 == 0xD:                                       # B<cond>, SVC, UDF
        cond = (hw >> 8) & 0xF
        if cond == 0xF:
            ins.ops = (("svc", hw & 0xFF),)
        elif cond == 0xE:
            ins.flow = ("trap",)
        else:
            ins.cond = cond
            ins.flow = ("b", (pc + (_sext(hw & 0xFF, 8) << 1)) & M32, "thumb")
    elif top5 == 0x1C:                                          # B
        ins.flow = ("b", (pc + (_sext(hw & 0x7FF, 11) << 1)) & M32, "thumb")
    elif top5 == 0x1E:                                          # BL / BLX prefix
        hw2 = read16(addr + 2)
        if hw2 is not None and hw2 >> 11 in (0x1F, 0x1D):
            t = (pc + ((_sext(hw & 0x7FF, 11) << 12) | ((hw2 & 0x7FF) << 1))) & M32
            ins.size = 4
            ins.flow = ("bl", t, "thumb") if hw2 >> 11 == 0x1F else ("bl", t & ~3, "arm")
        else:
            ins.flow = ("trap",)
    else:
        ins.flow = ("trap",)
    return ins


# ════════════════════════════════════════════════════════════════════════════════════
# Symbolic values
# ════════════════════════════════════════════════════════════════════════════════════
#
# A value is None (unknown) or a tuple (kind, a, off):
#   ("c", v, 0)              constant v
#   ("tls", None, k)         thread-local storage base + k (command buffer at +0x80)
#   ("sp", None, k)          the function's entry stack pointer + k
#   ("arg", n, k)            argument n (r0-r3, then stack words) + k
#   ("mem", addr, k)         word loaded from address value `addr`, + k
#   ("ret", target, k)       return value of a call to `target`, + k
#   ("ipcout", (hdr, o), k)  word o of the response to the request with header hdr
#   ("svcout", (n, r), 0)    output register r of svc n (e.g. the handle from CreateEvent)
#   ("handle", name, 0)      session handle of the service/port named by value `name`
#   ("copy", src, 0)         command-buffer word filled by a copy helper from value `src`

INTERESTING = ("handle", "svcout", "ipcout")
_LOCAL_KINDS = ("sp", "tls", "ret", "ipcout", "svcout", "handle", "copy")
MAX_DEPTH = 5


def C(v: int) -> tuple:
    return ("c", v & M32, 0)


def _s32(v: int) -> int:
    v &= M32
    return v - (1 << 32) if v & 0x80000000 else v


def vadd(v, k: int):
    if v is None or k == 0:
        return v
    kind = v[0]
    if kind == "c":
        return ("c", (v[1] + k) & M32, 0)
    if kind in ("handle", "copy", "svcout"):
        return None
    return (kind, v[1], _s32(v[2] + k))


def depth(v) -> int:
    d = 0
    while v is not None and v[0] in ("mem", "handle", "copy"):
        d += 1
        v = v[1]
    return d


def has_kind(v, kinds) -> bool:
    while v is not None:
        if v[0] in kinds:
            return True
        if v[0] in ("mem", "handle", "copy"):
            v = v[1]
        else:
            return False
    return False


def has_args(v) -> bool:
    return has_kind(v, ("arg",))


def _shift(name: str, x: int, n: int) -> int:
    n &= 0xFF
    if name == "lsl":
        return (x << n) & M32 if n < 32 else 0
    if name == "lsr":
        return x >> n if n < 32 else 0
    if name == "asr":
        return (_s32(x) >> min(n, 31)) & M32
    return _ror(x, n)


def binop(name: str, a, b):
    if a is None or b is None:
        return None
    if a[0] == "c" and b[0] == "c":
        x, y = a[1], b[1]
        if name == "add":
            return C(x + y)
        if name == "sub":
            return C(x - y)
        if name == "and":
            return C(x & y)
        if name == "orr":
            return C(x | y)
        if name == "eor":
            return C(x ^ y)
        if name == "bic":
            return C(x & ~y)
        if name == "mul":
            return C(x * y)
        if name in SHIFTS:
            return C(_shift(name, x, y))
        return None
    if name == "add":
        if b[0] == "c":
            return vadd(a, _s32(b[1]))
        if a[0] == "c":
            return vadd(b, _s32(a[1]))
    if name == "sub" and b[0] == "c":
        return vadd(a, -_s32(b[1]))
    if name in ("orr", "eor", "lsl", "lsr", "asr", "ror") and b == ("c", 0, 0):
        return a
    return None


def ev(src, regs):
    t = src[0]
    if t == "k":
        return C(src[1])
    if t == "r":
        return regs[src[1]] if src[1] != 15 else None
    if t == "op":
        return binop(src[1], ev(src[2], regs), ev(src[3], regs))
    if t == "not":
        a = ev(src[1], regs)
        return C(~a[1]) if a is not None and a[0] == "c" else None
    return None


def subst(v, args, mem=None):
    """Rewrite a callee value in the caller's terms (args = caller values of r0-r3 and
    the stack-passed words). `mem(addr)` -> (known, value) reads caller memory that has
    no meaning outside the caller (its stack) or never changes (read-only data)."""
    if v is None:
        return None
    kind = v[0]
    if kind == "arg":
        n = v[1]
        return vadd(args[n], v[2]) if n < len(args) and args[n] is not None else None
    if kind in ("c", "tls", "ipcout", "svcout"):
        return v
    if kind == "mem":
        a = subst(v[1], args, mem)
        if a is None:
            return None
        if mem is not None:
            known, val = mem(a)
            if known:
                return vadd(val, v[2])
        if a[0] == "sp":
            return None
        return ("mem", a, v[2]) if depth(a) < MAX_DEPTH else None
    if kind in ("handle", "copy"):
        a = subst(v[1], args, mem)
        return (kind, a, 0) if a is not None else None
    return None                                   # sp / ret of the callee mean nothing here


def fmt(v) -> str:
    if v is None:
        return "?"
    kind, a, k = v
    plus = (f"+0x{k:x}" if k >= 0 else f"-0x{-k:x}") if k else ""
    if kind == "c":
        return f"0x{a:08x}"
    if kind == "tls":
        return f"tls{plus}"
    if kind == "sp":
        return f"sp{plus}"
    if kind == "arg":
        return f"a{a}{plus}"
    if kind == "mem":
        return f"[{fmt(a)}]{plus}"
    if kind == "ret":
        return f"ret(0x{a:08x}){plus}"
    if kind == "ipcout":
        hdr, o = a
        return (f"ipc(0x{hdr:08x})" if hdr is not None else "ipc(?)") + f".resp+0x{o:x}{plus}"
    if kind == "svcout":
        n, r = a
        return f"svc_{SVC_NAMES.get(n, hex(n))}.r{r}"
    if kind == "handle":
        return f"handle({fmt(a)})"
    if kind == "copy":
        return f"copy({fmt(a)})"
    return str(v)


# ════════════════════════════════════════════════════════════════════════════════════
# Abstract interpreter
# ════════════════════════════════════════════════════════════════════════════════════

_MISSING = object()


class State:
    __slots__ = ("regs", "stack", "tls", "heap", "resp")

    def __init__(self, regs, stack=None, tls=None, heap=None, resp=_MISSING):
        self.regs = regs
        self.stack = stack if stack is not None else {}
        self.tls = tls if tls is not None else {}
        self.heap = heap if heap is not None else {}
        self.resp = resp                     # header of the last SendSyncRequest, or _MISSING

    def copy(self) -> "State":
        return State(list(self.regs), dict(self.stack), dict(self.tls), dict(self.heap), self.resp)

    def same(self, o: "State") -> bool:
        return (self.regs == o.regs and self.stack == o.stack and self.tls == o.tls
                and self.heap == o.heap and self.resp == o.resp)


def _merge_dict(a: dict, b: dict) -> dict:
    out = {}
    for k in a.keys() | b.keys():
        x, y = a.get(k, _MISSING), b.get(k, _MISSING)
        out[k] = x if x == y else None
    return out


def merge(a: State, b: State) -> State:
    # A heap entry the paths disagree on is dropped: loads then fall back to the
    # symbolic contents of that address, which is still exact.
    heap = {k: v for k, v in a.heap.items() if k in b.heap and b.heap[k] == v}
    return State([x if x == y else None for x, y in zip(a.regs, b.regs)],
                 _merge_dict(a.stack, b.stack), _merge_dict(a.tls, b.tls),
                 heap, a.resp if a.resp == b.resp else None)


@dataclass
class Request:
    site: int
    header: object               # value of command word 0
    handle: object               # value of r0 at SendSyncRequest
    cmd: dict                    # command buffer words (offset from cmd start -> value)
    via: int | None = None       # SendSyncRequest wrapper, if called through one


@dataclass
class CallSite:
    target: int
    mode: str
    args: tuple                  # r0-r3, then 4 stack words
    stack: dict                  # caller's stack slots at the call


@dataclass
class FuncInfo:
    addr: int
    mode: str
    ninsns: int = 0
    svcs: dict = field(default_factory=dict)          # site -> (n, (r0..r3))
    requests: dict = field(default_factory=dict)      # site -> Request
    calls: dict = field(default_factory=dict)         # site -> CallSite
    effects: dict = field(default_factory=dict)       # (site, dest) -> value
    pointers: dict = field(default_factory=dict)      # (site, dest) -> constant data pointer stored
    copies: dict = field(default_factory=dict)        # (site, dest) -> argument / loaded word stored
    ret: object = _MISSING
    has_tls: bool = False
    problems: list = field(default_factory=list)


class Context:
    """What the interpreter knows about other functions."""

    def __init__(self, img: Image, entries: dict[int, str], sizes: dict[int, int] | None = None):
        self.img = img
        self.entries = entries
        self.sizes = sizes or {}
        self.leaf_returns: dict[int, object] = {}     # small call-free getters, analysed on demand
        self.probing: set[int] = set()
        self.summaries: dict[int, tuple] = {}         # func -> ((dest, value), ...)
        self.returns: dict[int, object] = {}          # func -> return value (callee terms)
        self.ssr_wrappers: set[int] = set()            # SendSyncRequest(handle=r0) wrappers
        self.ipc_funcs: set[int] = set()               # functions that do IPC themselves
        self._decoded: dict[tuple[int, str], Insn] = {}

    def decode(self, addr: int, mode: str) -> Insn:
        key = (addr, mode)
        ins = self._decoded.get(key)
        if ins is None:
            if not self.img.in_text(addr):
                ins = Insn(addr, 4 if mode == "arm" else 2, mode, flow=("trap",))
            elif mode == "arm":
                w = self.img.read32(addr)
                ins = decode_arm(w, addr) if w is not None else Insn(addr, 4, mode, flow=("trap",))
            else:
                ins = decode_thumb(addr, self.img.read16)
            self._decoded[key] = ins
        return ins

    def literal_target(self, addr_value) -> tuple[int, str] | None:
        if addr_value is None or addr_value[0] != "c":
            return None
        t = self.img.read32(addr_value[1])
        if t is None:
            return None
        return (t & ~1, "thumb") if t & 1 else (t, "arm")

    def veneer(self, target: int, mode: str) -> tuple[int, str]:
        """Follow `ldr pc, [pc, #-4]` / Thumb `bx pc` interworking veneers."""
        for _ in range(3):
            if mode == "arm":
                w = self.img.read32(target)
                if w == 0xE51FF004:
                    lit = self.img.read32(target + 4)
                    if lit is None:
                        break
                    target, mode = (lit & ~1, "thumb") if lit & 1 else (lit, "arm")
                    continue
                if w is not None and (w & 0x0F000000) == 0x0A000000 and w >> 28 == 0xE and target not in self.entries:
                    break
            else:
                if self.img.read16(target) == 0x4778:            # bx pc ; nop ; (ARM)
                    target, mode = (target + 4) & ~3, "arm"
                    continue
            break
        return target, mode


class Interpreter:
    MAX_BLOCK_VISITS = 12
    MAX_INSNS = 6000

    def __init__(self, ctx: Context):
        self.ctx = ctx

    # ── memory ────────────────────────────────────────────────────────────────
    def load(self, st: State, a, size: int):
        if a is None:
            return None
        kind = a[0]
        if kind == "sp":
            off = a[2]
            if off in st.stack:
                return st.stack[off] if size == 4 else None
            if off >= 0 and off % 4 == 0 and size == 4:
                return ("arg", 4 + off // 4, 0)
            return None
        if kind == "tls":
            off = a[2]
            if off in st.tls:
                return st.tls[off] if size == 4 else None
            if st.resp is not _MISSING and off >= CMDBUF and size == 4:
                return ("ipcout", (st.resp, off - CMDBUF), 0)
            return None
        if size != 4:
            return None
        if a in st.heap:
            return st.heap[a]
        if kind == "c" and self.ctx.img.read_only(a[1]):
            w = self.ctx.img.read32(a[1])
            return C(w) if w is not None else None
        if kind in ("ipcout", "svcout", "handle", "copy"):
            return None
        return ("mem", a, 0) if depth(a) < MAX_DEPTH else None

    def mem_lookup(self, stack: dict, a) -> tuple[bool, object]:
        """Caller memory for subst(): stack slots and read-only constants."""
        if a[0] == "sp":
            off = a[2]
            if off in stack:
                return True, stack[off]
            return True, (("arg", 4 + off // 4, 0) if off >= 0 and off % 4 == 0 else None)
        if a[0] == "c" and self.ctx.img.read_only(a[1]):
            w = self.ctx.img.read32(a[1])
            return True, (C(w) if w is not None else None)
        return False, None

    def leaf_return(self, target: int, mode: str):
        """Return value of a small function without system calls (e.g. `ldr r0, =g; bx lr`)."""
        ctx = self.ctx
        r = ctx.leaf_returns.get(target, _MISSING)
        if r is not _MISSING or target in ctx.leaf_returns:
            return r
        size = ctx.sizes.get(target)
        if size is None or size > 0x40 or target in ctx.probing:
            return _MISSING
        ctx.probing.add(target)
        try:
            info = self.run(target, mode)
        finally:
            ctx.probing.discard(target)
        r = info.ret if not info.svcs and _returnable(info.ret) else _MISSING
        ctx.leaf_returns[target] = r
        return r

    def store(self, st: State, a, value, size: int, site: int, info: FuncInfo):
        if a is None:
            return
        kind = a[0]
        if size != 4:
            value = None
        if kind == "sp":
            st.stack[a[2] - a[2] % 4 if size != 4 else a[2]] = value
            return
        if kind == "tls":
            st.tls[a[2] - a[2] % 4 if size != 4 else a[2]] = value
            return
        if kind in ("ipcout", "svcout", "handle", "copy"):
            return
        st.heap[a] = value
        if value is not None:
            k = value[0]
            if k in INTERESTING:
                info.effects[(site, a)] = value
            elif k == "c":
                if self.ctx.img.segment(value[1]) in ("data", "bss"):
                    info.pointers[(site, a)] = value
            elif (k == "arg" or (k == "mem" and value[2] == 0)) and not has_kind(value, _LOCAL_KINDS):
                info.copies[(site, a)] = value

    # ── calls and system calls ───────────────────────────────────────────────
    def call_args(self, st: State) -> tuple:
        sp = st.regs[13]
        extra = []
        for i in range(4):
            extra.append(self.load(st, vadd(sp, 4 * i), 4) if sp is not None and sp[0] == "sp" else None)
        return tuple(st.regs[:4]) + tuple(extra)

    def do_call(self, st: State, target: int, mode: str, site: int, info: FuncInfo):
        ctx = self.ctx
        target, mode = ctx.veneer(target, mode)
        args = self.call_args(st)
        info.calls[site] = CallSite(target, mode, args, dict(st.stack))
        if target in ctx.ssr_wrappers:
            self.record_request(st, site, info, via=target)
        r0, r1 = st.regs[0], st.regs[1]
        if r0 is not None and r0[0] == "tls" and r1 is not None:
            st.tls[r0[2]] = ("copy", r1, 0)                 # copy helper writing into the command buffer
        for r in (0, 1, 2, 3, 12, 14):
            st.regs[r] = None
        st.heap.clear()
        if target in ctx.ipc_funcs and target not in ctx.ssr_wrappers:
            st.tls.clear()
            st.resp = _MISSING
        ret = ctx.returns.get(target, _MISSING)
        if ret is _MISSING:
            ret = self.leaf_return(target, mode)
        stack = st.stack

        def mem(a):
            return self.mem_lookup(stack, a)

        st.regs[0] = subst(ret, args, mem) if ret is not _MISSING else ("ret", target, 0)
        for dest, value in ctx.summaries.get(target, ()):
            d, v = subst(dest, args, mem), subst(value, args, mem)
            if d is not None and v is not None:
                self.store(st, d, v, 4, site, info)

    def record_request(self, st: State, site: int, info: FuncInfo, via: int | None = None):
        cmd = {k - CMDBUF: v for k, v in st.tls.items() if k >= CMDBUF}
        info.requests[site] = Request(site, cmd.get(0), st.regs[0], cmd, via)
        hdr = cmd.get(0)
        st.tls.clear()
        st.resp = hdr[1] if hdr is not None and hdr[0] == "c" else None
        if st.resp == SRV_GET_SERVICE_HANDLE:
            # The service name travels inline in words 1-2; the session handle comes back
            # in response word 3.
            name = cmd.get(4)
            if name is not None and (name[0] == "copy" or (name[0] == "mem" and name[2] == 0)):
                st.tls[CMDBUF + 0xC] = ("handle", name[1], 0)

    def do_svc(self, st: State, n: int, site: int, info: FuncInfo):
        info.svcs[site] = (n, tuple(st.regs[:4]))
        if n == SVC_SEND_SYNC_REQUEST:
            self.record_request(st, site, info)
            st.regs[0] = None
            return
        r1 = st.regs[1]
        for r in (0, 1, 2, 3):
            st.regs[r] = None
        if n == 0x02:
            st.regs[4] = st.regs[5] = None
        if n == SVC_CONNECT_TO_PORT and r1 is not None:
            st.regs[1] = ("handle", r1, 0)
        elif n in SVC_R1_OUTPUT:
            st.regs[1] = ("svcout", (n, 1), 0)
        elif n == 0x28:
            st.regs[0] = ("svcout", (n, 0), 0)

    # ── one instruction's IR ─────────────────────────────────────────────────
    def exec_ops(self, ins: Insn, st: State, info: FuncInfo):
        regs = st.regs
        for op in ins.ops:
            t = op[0]
            if t == "set":
                v = ev(op[2], regs)
                regs[op[1]] = v if depth(v) < MAX_DEPTH else None
            elif t == "ld":
                regs[op[1]] = self.load(st, ev(op[2], regs), op[3])
            elif t == "st":
                self.store(st, ev(op[2], regs), regs[op[1]] if op[1] >= 0 else None, op[3], ins.addr, info)
            elif t == "kill":
                if 0 <= op[1] < 15:
                    regs[op[1]] = None
            elif t == "tls":
                regs[op[1]] = ("tls", None, 0)
                info.has_tls = True
            elif t == "svc":
                self.do_svc(st, op[1], ins.addr, info)

    # ── one function ─────────────────────────────────────────────────────────
    def discover(self, entry: int, mode: str) -> tuple[dict[int, Insn], set[int], set[int], list[str]]:
        """Reachable instructions, block leaders, and addresses whose next instruction is
        another function's entry (fall-through tail calls)."""
        ctx = self.ctx
        insns: dict[int, Insn] = {}
        leaders = {entry}
        fall: set[int] = set()
        problems: list[str] = []
        work = [entry]

        def local(t: int, tmode: str) -> bool:
            return tmode == mode and t != entry and ctx.entries.get(t) is None and ctx.img.in_text(t)

        while work:
            a = work.pop()
            while a not in insns:
                if len(insns) >= self.MAX_INSNS:
                    problems.append("instruction limit")
                    return insns, leaders, fall, problems
                ins = ctx.decode(a, mode)
                insns[a] = ins
                fl = ins.flow
                nxt = a + ins.size
                cond = ins.cond != AL
                if fl is not None:
                    kind = fl[0]
                    if kind == "b":
                        if local(fl[1], fl[2]):
                            leaders.add(fl[1])
                            work.append(fl[1])
                        if not cond:
                            break
                        leaders.add(nxt)
                    elif kind in ("jtab_add", "jtab_ldr"):
                        for t in self.jump_targets(ins):
                            leaders.add(t)
                            work.append(t)
                        if not cond:
                            break
                        leaders.add(nxt)
                    elif kind not in ("bl", "blxr"):
                        if not cond:
                            break
                        leaders.add(nxt)
                if nxt != entry and ctx.entries.get(nxt) is not None:
                    fall.add(a)
                    break
                a = nxt
        return insns, leaders, fall, problems

    def jump_targets(self, ins: Insn) -> list[int]:
        img = self.ctx.img
        table = ins.addr + 8 if ins.mode == "arm" else ins.addr + 4
        out = []
        for i in range(256):
            w = img.read32(table + 4 * i)
            if ins.flow[0] == "jtab_add":
                if w is None or w >> 24 != 0xEA:
                    break
                out.append(table + 4 * i)
            else:
                if w is None or not img.in_text(w) or w & 3:
                    break
                out.append(w)
        return out

    def run(self, entry: int, mode: str) -> FuncInfo:
        ctx = self.ctx
        info = FuncInfo(entry, mode)
        insns, leaders, fall, info.problems = self.discover(entry, mode)
        info.ninsns = len(insns)
        regs0 = ([("arg", 0, 0), ("arg", 1, 0), ("arg", 2, 0), ("arg", 3, 0)] + [None] * 9
                 + [("sp", None, 0), None, None, None])
        states: dict[int, State] = {entry: State(regs0)}
        visits: dict[int, int] = defaultdict(int)
        work = [entry]
        rets: dict[int, object] = {}
        while work:
            start = work.pop()
            visits[start] += 1
            st = states[start].copy()
            a = start
            succ: list[tuple[int, State]] = []
            # A predicated instruction splits the state in two (condition true / false)
            # until the flags change or a branch on that condition resolves it.
            split: list | None = None                    # [cond, state_if_true, state_if_false]
            while a in insns:
                ins = insns[a]
                c = ins.cond
                if split is not None and (c == AL or c >> 1 != split[0] >> 1):
                    st = merge(split[1], split[2])
                    split = None
                if c != AL:
                    if split is None:
                        split = [c, st, st.copy()]
                    cur = split[1] if c == split[0] else split[2]
                    other = split[2] if c == split[0] else split[1]
                else:
                    cur = st
                    other = None
                self.exec_ops(ins, cur, info)
                fl = ins.flow
                nxt = a + ins.size
                end = False
                if fl is not None:
                    kind = fl[0]
                    if kind in ("bl", "blxr"):
                        if kind == "bl":
                            self.do_call(cur, fl[1], fl[2], a, info)
                        else:
                            tv = cur.regs[fl[1]]
                            if tv is not None and tv[0] == "c":
                                self.do_call(cur, tv[1] & ~1, "thumb" if tv[1] & 1 else "arm", a, info)
                            else:
                                for r in (0, 1, 2, 3, 12, 14):
                                    cur.regs[r] = None
                                cur.heap.clear()
                    else:
                        taken = cur
                        if kind == "b":
                            t, tmode = fl[1], fl[2]
                            if t in leaders and tmode == mode:
                                succ.append((t, taken.copy()))
                            else:                                   # tail call
                                self.do_call(taken, t, tmode, a, info)
                                rets[a] = taken.regs[0]
                        elif kind in ("jtab_add", "jtab_ldr"):
                            for t in self.jump_targets(ins):
                                succ.append((t, taken.copy()))
                        elif kind == "ret":
                            rets[a] = taken.regs[0]
                        elif kind in ("ldrpc_lit", "bxr"):
                            tv = ev(fl[1], taken.regs) if kind == "ldrpc_lit" else taken.regs[fl[1]]
                            tgt = ctx.literal_target(tv) if kind == "ldrpc_lit" else (
                                (tv[1] & ~1, "thumb" if tv[1] & 1 else "arm") if tv is not None and tv[0] == "c" else None)
                            if tgt is not None:
                                self.do_call(taken, tgt[0], tgt[1], a, info)
                                rets[a] = taken.regs[0]
                        if other is not None:
                            st, split = other, None             # fall through on the false side
                        else:
                            end = True
                if split is not None and ins.sets_flags:
                    st = merge(split[1], split[2])
                    split = None
                if end:
                    break
                if split is not None and (a in fall or nxt in leaders):
                    st = merge(split[1], split[2])
                    split = None
                if a in fall:
                    self.do_call(st, nxt, mode, a, info)
                    rets[a] = st.regs[0]
                    break
                if nxt in leaders:
                    succ.append((nxt, st))
                    break
                a = nxt
            for t, s in succ:
                old = states.get(t)
                if old is None:
                    states[t] = s
                    work.append(t)
                elif visits[t] < self.MAX_BLOCK_VISITS:
                    m = merge(old, s)
                    if not m.same(old):
                        states[t] = m
                        work.append(t)
        if rets:
            vals = list(rets.values())
            info.ret = vals[0] if all(v == vals[0] for v in vals) else None
        return info


# ════════════════════════════════════════════════════════════════════════════════════
# Whole-program passes
# ════════════════════════════════════════════════════════════════════════════════════

WRAPPER_MAX_INSNS = 16
RESOLVE_DEPTH = 5
CALLER_CAP = 64
RESOLVE_CAP = 48
# svc -> register holding the handle worth reporting (WaitSynchronizationN: handle array)
TRACKED_SVC_ARGS = {0x18: 0, 0x19: 0, 0x1B: 0, 0x1F: 0, 0x20: 0, 0x24: 0, 0x25: 1}


def _returnable(v) -> bool:
    if v is None or v is _MISSING:
        return False
    if v[0] in INTERESTING or v[0] in ("tls", "c", "arg"):
        return True
    return v[0] == "mem" and not has_kind(v, ("sp", "ret", "tls", "ipcout", "svcout"))


def _root(v):
    while v is not None and v[0] == "mem":
        v = v[1]
    return v


class Program:
    def __init__(self, img: Image, funcs: dict[int, tuple[int, str]], callers: dict[int, set[int]]):
        self.img = img
        self.funcs = funcs
        self.callers = callers
        self.ctx = Context(img, {a: m for a, (s, m) in funcs.items()}, {a: s for a, (s, m) in funcs.items()})
        self.interp = Interpreter(self.ctx)
        self.cache: dict[int, FuncInfo] = {}
        self.visited: set[int] = set()
        self.svc_wrappers: dict[int, int] = {}         # func -> svc number
        self.propagated: set[int] = set()              # functions whose summary is computed
        self.tls_getters: set[int] = set()

    def mode(self, addr: int) -> str:
        return self.funcs.get(addr, (0, "arm"))[1]

    def analyze(self, addr: int, fresh: bool = False) -> FuncInfo:
        info = None if fresh else self.cache.get(addr)
        if info is None:
            info = self.interp.run(addr, self.mode(addr))
            self.cache[addr] = info
            self.visited.add(addr)
        return info

    def ncallers(self, addr: int) -> int:
        return len(self.callers.get(addr, ()))

    def global_location(self, v) -> bool:
        """A place that outlives any one call: a data/bss address, or a chain of loads
        rooted at one (a field of an object a global points to)."""
        if v is None or has_kind(v, ("arg", "sp", "tls", "ret", "ipcout", "svcout", "handle", "copy")):
            return False
        r = _root(v)
        return r is not None and r[0] == "c" and self.img.segment(r[1]) in ("data", "bss")

    # ── pass 0: raw pattern scan ─────────────────────────────────────────────
    def raw_seeds(self) -> set[int]:
        img, lo = self.img, self.img.layout
        starts = sorted(self.funcs)
        hits: set[int] = set()

        def owner(a: int) -> int | None:
            i = bisect.bisect_right(starts, a) - 1
            if i >= 0 and a < starts[i] + self.funcs[starts[i]][0]:
                return starts[i]
            return None

        text = memoryview(img.code)[lo.text[0] - img.base:(lo.text[1] - img.base) & ~3].cast("I")
        for i, w in enumerate(text):
            if ((w >> 24) & 0xF == 0xF and w >> 28 != 0xF) or (w & 0x0FFF0FFF) == 0x0E1D0F70:
                f = owner(img.base + 4 * i)
                if f is not None and self.funcs[f][1] == "arm":
                    hits.add(f)
        for f, (size, mode) in self.funcs.items():
            if mode == "thumb":
                for a in range(f, f + size - 1, 2):
                    hw = img.read16(a)
                    if hw is not None and hw >> 8 == 0xDF:
                        hits.add(f)
                        break
        return hits

    # ── pass 1+2: classify, then propagate summaries up the call graph ──────
    def summary_of(self, info: FuncInfo) -> tuple:
        """What a function stores through its arguments or from its arguments: handles,
        svc/IPC outputs, and argument values/loaded words copied somewhere."""
        out = set()
        for table in (info.effects, info.copies):
            for (site, dest), value in table.items():
                if (has_args(dest) or has_args(value)) and not has_kind(dest, ("sp", "ret", "tls")):
                    out.add((dest, value))
        return tuple(sorted(out, key=repr))

    def classify(self, seeds: set[int]) -> None:
        ctx = self.ctx
        for a in seeds:
            info = self.analyze(a)
            if len(info.svcs) == 1 and not info.calls and not info.has_tls and info.ninsns <= WRAPPER_MAX_INSNS:
                (site, (n, regs)), = info.svcs.items()
                if any(r is not None and r[0] not in ("arg", "c") for r in regs):
                    continue                    # works on state of its own: not a plain wrapper
                self.svc_wrappers[a] = n
                if n == SVC_SEND_SYNC_REQUEST and regs[0] == ("arg", 0, 0):
                    ctx.ssr_wrappers.add(a)
            if info.ret is not _MISSING and info.ret is not None and info.ret[0] == "tls" and not info.svcs:
                self.tls_getters.add(a)
            if info.requests:
                ctx.ipc_funcs.add(a)

    def run_all(self, progress=None) -> None:
        ctx = self.ctx
        seeds = self.raw_seeds()
        self.classify(seeds)
        # Callers of SendSyncRequest wrappers and TLS getters hold request sites as well.
        extra: set[int] = set()
        for f in self.tls_getters:
            ctx.returns[f] = self.analyze(f).ret
        for f in self.tls_getters | ctx.ssr_wrappers:
            extra |= self.callers.get(f, set())
        if extra:
            self.cache.clear()
            self.classify(extra)
            seeds |= extra
        self.seeds = seeds
        self.propagate(set(seeds), progress)
        # Second round: functions that address the globals some request reads its handle
        # from (directly or as base + offset) without them being on a call path we
        # followed, e.g. a setter copying one session handle into another global.
        targets = self.unresolved_handle_globals()
        if targets:
            index = self.data_refs()
            more: set[int] = set()
            for g in targets:
                lo = bisect.bisect_left(index, (g - 0x40, -1))
                hi = bisect.bisect_right(index, (g, 1 << 40))
                refs = {f for _, f in index[lo:hi]}
                if len(refs) <= 400:
                    more |= refs
            more -= self.propagated
            if more:
                self.propagate(more, progress)
        self.cache.clear()
        self._facts = None
        for f in sorted(self.visited):
            self.analyze(f)

    def data_refs(self) -> list[tuple[int, int]]:
        """(data/bss address, function) for every literal word in a function's extent
        (code plus the literal pool up to the next function)."""
        img, lo = self.img, self.img.layout
        starts = sorted(self.funcs)
        out = []
        for i, f in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else lo.text[1]
            end = min(end, f + 0x4000)
            a = (f + 3) & ~3
            data = img.code[a - img.base:end - img.base]
            for k in range(0, len(data) - 3, 4):
                w = int.from_bytes(data[k:k + 4], "little")
                if lo.data[0] <= w < lo.bss[1]:
                    out.append((w, f))
        out.sort()
        return out

    def unresolved_handle_globals(self) -> set[int]:
        facts = self.facts()
        out = set()
        for f in sorted(self.visited):
            info = self.analyze(f)
            for req in info.requests.values():
                services, locs = self.services_for_handle(f, req.handle, facts)
                if services:
                    continue
                for v in self.resolve_up(f, req.handle):
                    r = _root(v)
                    if r is not None and r[0] == "c" and self.img.segment(r[1]) in ("data", "bss"):
                        out.add(r[1])
        self._facts = None
        return out

    def propagate(self, frontier: set[int], progress=None) -> None:
        ctx = self.ctx
        for level in range(RESOLVE_DEPTH + 2):
            changed = set()
            for f in sorted(frontier):
                info = self.analyze(f, fresh=True)
                self.propagated.add(f)
                if info.requests:
                    ctx.ipc_funcs.add(f)
                s = self.summary_of(info)
                if s != ctx.summaries.get(f, ()):
                    if s:
                        ctx.summaries[f] = s
                    else:
                        ctx.summaries.pop(f, None)
                    changed.add(f)
                r = info.ret if _returnable(info.ret) else _MISSING
                if r != ctx.returns.get(f, _MISSING):
                    if r is _MISSING:
                        ctx.returns.pop(f, None)
                    else:
                        ctx.returns[f] = r
                    changed.add(f)
            if progress:
                progress(level, len(frontier), len(changed))
            if not changed:
                break
            frontier = set()
            for f in changed:
                cs = self.callers.get(f, set())
                if len(cs) <= CALLER_CAP:
                    frontier |= cs

    # ── resolution through callers ──────────────────────────────────────────
    def resolve_up(self, func: int, value, depth: int = 0) -> list:
        if value is None:
            return []
        if not has_args(value):
            return [value]
        if depth >= RESOLVE_DEPTH:
            return []
        cs = self.callers.get(func, set())
        if len(cs) > CALLER_CAP:
            return []
        out: list = []
        for c in sorted(cs):
            info = self.analyze(c)
            for site in sorted(info.calls):
                call = info.calls[site]
                if call.target != func:
                    continue
                stack = call.stack
                v2 = subst(value, call.args, lambda a: self.interp.mem_lookup(stack, a))
                if v2 is not None:
                    for r in self.resolve_up(c, v2, depth + 1):
                        if r not in out:
                            out.append(r)
                if len(out) >= RESOLVE_CAP:
                    return out
        return out

    def facts(self) -> dict:
        """Global locations -> {value: set(functions that store it)}."""
        if getattr(self, "_facts", None) is not None:
            return self._facts
        out: dict = defaultdict(lambda: defaultdict(set))
        ptr: dict = defaultdict(set)
        for f in sorted(self.visited):
            info = self.analyze(f)
            for (site, dest), value in info.effects.items():
                if self.global_location(dest) and not has_args(value):
                    out[dest][value].add(f)
            for (site, dest), value in info.pointers.items():
                if self.global_location(dest):
                    ptr[dest].add(value)
            for (site, dest), value in info.copies.items():
                if self.global_location(dest) and value[0] == "mem" and self.global_location(value[1]):
                    out[dest][value].add(f)
        self._facts, self._pointers = out, ptr
        return out

    def expand(self, loc) -> list:
        """A handle location plus what it means when the address itself comes from a
        global that is only ever set to constant pointers ([G] -> P)."""
        out = [loc]
        if loc[0] == "mem":
            self.facts()
            for p in sorted(self._pointers.get(loc[1], ()), key=repr):
                out.append(vadd(p, loc[2]))
        return out

    def name_of(self, v) -> str | None:
        """String a name value points at (directly, or through an initialized pointer)."""
        if v is None:
            return None
        if v[0] == "c":
            return self.img.cstring(v[1])
        if v[0] == "mem" and v[2] == 0 and v[1][0] == "c" and self.img.initialized(v[1][1]):
            p = self.img.read32(v[1][1])
            return self.img.cstring(p) if p is not None else None
        return None

    def provenance_service(self, v) -> str | None:
        if v is None:
            return None
        if v[0] == "handle":
            return self.name_of(v[1])
        if v[0] == "ipcout" and v[1][0] is not None:
            return SESSION_FROM_RESPONSE.get(v[1])
        return None

    def location_services(self, loc, facts, depth: int = 0) -> list[str]:
        """Services whose session handle is stored at `loc`, following copies between
        globals and constant pointers to the location."""
        out: list[str] = []
        for l in self.expand(loc):
            for prov in facts.get(l, {}):
                if prov[0] == "mem" and prov[2] == 0:
                    if depth < 3:
                        out += [s for s in self.location_services(prov[1], facts, depth + 1) if s not in out]
                    continue
                s = self.provenance_service(prov)
                if s and s not in out:
                    out.append(s)
        return out

    def services_for_handle(self, func: int, handle, facts) -> tuple[list[str], list[str]]:
        """(services, handle locations) for the handle value used at a request site."""
        services: list[str] = []
        locs: list[str] = []
        for v in self.resolve_up(func, handle):
            s = None
            if v[0] == "mem" and v[2] == 0:
                locs.append(fmt(v[1]))
                for s in self.location_services(v[1], facts):
                    if s not in services:
                        services.append(s)
                continue
            s = self.provenance_service(v)
            locs.append(fmt(v))
            if s and s not in services:
                services.append(s)
        return services, locs


# ════════════════════════════════════════════════════════════════════════════════════
# Reports
# ════════════════════════════════════════════════════════════════════════════════════

COLUMNS = ("function", "mode", "service", "header", "command", "callers", "site", "handle", "note")


def _hdr(v) -> int | None:
    return v[1] if v is not None and v[0] == "c" else None


def request_rows(prog: Program, detail: bool = False) -> list[dict]:
    facts = prog.facts()
    pending = []
    for f in sorted(prog.visited):
        info = prog.analyze(f)
        for site in sorted(info.requests):
            req = info.requests[site]
            hdr = _hdr(req.header)
            services, locs = prog.services_for_handle(f, req.handle, facts)
            pending.append((f, info, site, req, hdr, services, locs))
    # Handles whose service could not be traced. SDK client code for one service sits
    # together and reads its handle the same way, so neighbouring request functions
    # (within 0x200 bytes) with the same handle expression form a cluster; a cluster
    # takes the service that headers known to only one service vote for, if at least two
    # votes agree and no more than a third disagree. A lone guess is kept only for
    # distinctive command ids (>= 0x400, e.g. fs:USER 0x08xx).
    unresolved = [p for p in pending if not p[5]]
    unresolved.sort(key=lambda p: (p[0], p[2]))
    cluster_of: dict[int, int] = {}
    clusters: list[list] = []
    for p in unresolved:
        key = ",".join(p[6]) if p[6] else fmt(p[3].handle)
        if clusters and p[0] - clusters[-1][-1][0][0] <= 0x200 and clusters[-1][-1][1] == key:
            clusters[-1].append((p, key))
        else:
            clusters.append([(p, key)])
        cluster_of[p[2]] = len(clusters) - 1
    by_key: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(clusters):
        if c[0][1] and not c[0][1].startswith("[a") and c[0][1] != "?":
            by_key[c[0][1]].append(i)              # same global handle: one vote pool
    verdict: dict[int, str] = {}
    pools = list(by_key.values()) + [[i] for i, c in enumerate(clusters) if all(i not in v for v in by_key.values())]
    for pool in pools:
        votes: dict[str, int] = defaultdict(int)
        for i in pool:
            for p, key in clusters[i]:
                g = guess_service(p[4])
                if g:
                    votes[g] += 1
        if votes:
            best, n = max(votes.items(), key=lambda kv: kv[1])
            if n >= 2 and n * 3 >= 2 * sum(votes.values()):
                for i in pool:
                    verdict[i] = best
    # A lone untraced request next to traced ones of a single service (within 0x400
    # bytes either side) whose table has exactly its header.
    resolved = sorted((p[0], "|".join(sorted(p[5]))) for p in pending if p[5])
    rstarts = [r[0] for r in resolved]

    def between(func: int, hdr: int | None) -> str | None:
        i = bisect.bisect_left(rstarts, func)
        near = [resolved[j][1] for j in (i - 1, i) if 0 <= j < len(resolved)
                and abs(resolved[j][0] - func) <= 0x400]
        if not near or len(set(near)) != 1 or "|" in near[0] or hdr is None:
            return None
        return near[0] if hdr in commands_of(near[0]) else None

    rows = []
    for f, info, site, req, hdr, services, locs in pending:
        note = []
        if services:
            service = "|".join(sorted(services))
        else:
            g = verdict.get(cluster_of.get(site, -1))
            own = guess_service(hdr)
            known = hdr is not None and any(hdr in t for t in SERVICE_COMMANDS.values())
            if g is not None and known and hdr not in commands_of(g):
                g = None                                   # its own header says otherwise
            if g is not None:
                note.append("service by vote of neighbouring requests")
            elif between(f, hdr) is not None:
                g = between(f, hdr)
                note.append("service of the neighbouring request functions")
            elif own is not None and (hdr >> 16 >= 0x400 or cluster_of.get(site, -1) in verdict):
                g = own
                note.append("service from the header alone")
            service = g + "?" if g else ""
        cmd, cnote = command_name(service, hdr)
        if cnote:
            note.append(cnote)
        if req.via is not None:
            note.append(f"via 0x{req.via:08x}")
        if hdr == SRV_GET_SERVICE_HANDLE:
            name = req.cmd.get(4)
            if name is not None and name[0] in ("copy", "mem"):
                names = sorted({n for n in (prog.name_of(x) for x in prog.resolve_up(f, name[1])) if n})
                if names:
                    note.append("names " + ",".join(names))
        if detail:
            words = " ".join(f"w{k // 4}={fmt(v)}" for k, v in sorted(req.cmd.items()) if k and v is not None)
            if words:
                note.append(words)
        rows.append({
            "function": f"0x{f:08x}", "mode": info.mode, "service": service,
            "header": f"0x{hdr:08x}" if hdr is not None else "?", "command": cmd,
            "callers": str(prog.ncallers(f)), "site": f"0x{site:08x}",
            "handle": ",".join(locs[:4]) + (",…" if len(locs) > 4 else "") if locs else fmt(req.handle),
            "note": "; ".join(note),
        })
    return rows


def svc_rows(prog: Program) -> list[dict]:
    rows = []
    for f in sorted(prog.visited):
        info = prog.analyze(f)
        for site in sorted(info.svcs):
            n, regs = info.svcs[site]
            wrapper = prog.svc_wrappers.get(f) == n
            note = ["wrapper" if wrapper else ("ipc" if site in info.requests else "inline")]
            handle = ""
            reg = TRACKED_SVC_ARGS.get(n)
            if reg is not None and regs[reg] is not None:
                vals = prog.resolve_up(f, regs[reg])
                handle = ",".join(fmt(v) for v in vals[:6]) + (",…" if len(vals) > 6 else "")
            rows.append({
                "function": f"0x{f:08x}", "mode": info.mode, "service": "svc",
                "header": f"0x{n:02x}", "command": SVC_NAMES.get(n, "?"),
                "callers": str(prog.ncallers(f)), "site": f"0x{site:08x}", "handle": handle,
                "note": "; ".join(note),
            })
    return rows


def handle_rows(prog: Program) -> list[dict]:
    """Every global location that receives a session handle, svc output or IPC result."""
    rows = []
    for loc, values in sorted(prog.facts().items(), key=lambda kv: repr(kv[0])):
        for v, funcs in sorted(values.items(), key=lambda kv: repr(kv[0])):
            service = prog.provenance_service(v) or ""
            if v[0] == "handle" and not service:
                service = "?"
            rows.append({"location": fmt(loc), "value": fmt(v), "service": service,
                         "stored_by": ",".join(f"0x{x:08x}" for x in sorted(funcs))})
    return rows


def write_tsv(rows: list[dict], columns, out) -> None:
    out.write("\t".join(columns) + "\n")
    for r in rows:
        out.write("\t".join(str(r.get(c, "")) for c in columns) + "\n")


def _open_out(path: str | None):
    if not path or path == "-":
        return sys.stdout
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    return open(path, "w", encoding="utf-8", newline="")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--code", default=os.path.join(ROOT, "build", "code_v130_dec.bin"),
                    help="decompressed code image (or a compressed code.bin with --exheader)")
    ap.add_argument("--exheader", default=os.path.join(ROOT, "baserom", "update_v130", "exheader.bin"))
    ap.add_argument("--versions", default=os.path.join(ROOT, "config", "versions.json"))
    ap.add_argument("--functions", default=os.path.join(ROOT, "config", "functions.tsv"))
    ap.add_argument("--callgraph", default=os.path.join(ROOT, "build", "export", "callgraph.tsv"),
                    help="Ghidra call graph export; scanned from the code when missing")
    ap.add_argument("-o", "--output", help="request + svc table (TSV); default stdout")
    ap.add_argument("--handles", help="also write the handle-global table (TSV)")
    ap.add_argument("--no-svc", action="store_true", help="leave svc sites out of the main table")
    ap.add_argument("--detail", action="store_true", help="add command-buffer words to the note column")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)

    layout = load_layout(args.exheader, args.versions)
    if layout is None:
        print("error: no segment layout (need --exheader or config/versions.json)", file=sys.stderr)
        return 2
    if not os.path.exists(args.code):
        print(f"error: {args.code} not found. Build it from your dump with tools/codebin2elf.load_code, "
              "or pass --code baserom/update_v130/code.bin (decompressed using --exheader)", file=sys.stderr)
        return 2
    img = load_image(args.code, layout, args.exheader)
    funcs = load_functions(args.functions)
    callers = load_callgraph(args.callgraph) if os.path.exists(args.callgraph) else scan_callgraph(img, funcs)
    prog = Program(img, funcs, callers)

    def progress(level, n, changed):
        if not args.quiet:
            print(f"level {level}: analyzed {n} functions, {changed} summaries changed", file=sys.stderr)

    prog.run_all(progress)
    rows = request_rows(prog, args.detail) + ([] if args.no_svc else svc_rows(prog))
    rows.sort(key=lambda r: (r["function"], r["site"]))
    out = _open_out(args.output)
    try:
        write_tsv(rows, COLUMNS, out)
    finally:
        if out is not sys.stdout:
            out.close()
    if args.handles:
        out = _open_out(args.handles)
        try:
            write_tsv(handle_rows(prog), ("location", "value", "service", "stored_by"), out)
        finally:
            if out is not sys.stdout:
                out.close()
    if not args.quiet:
        nreq = sum(1 for r in rows if r["service"] != "svc")
        print(f"{nreq} request sites, {len(rows) - nreq} svc sites, {len(prog.visited)} functions analyzed",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
