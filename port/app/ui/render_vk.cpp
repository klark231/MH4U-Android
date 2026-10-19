// The in-game menu over Vulkan. Azahar's renderer presents by itself, so the menu is drawn
// through its present overlay (video_core/renderer_vulkan/vk_overlay.h, from azahar.patch): once a
// frame, on the emulation thread, the renderer asks for commands to record in the main window's
// present render pass after the screens, and runs them on its scheduler's thread. Dear ImGui's
// Vulkan backend loads its functions through Azahar's loader (vkGetInstanceProcAddr), uploads
// its textures on the renderer's queue under the renderer's queue lock, and keeps a set of buffers
// per frame in flight.

// the backend without Vulkan's prototypes: its functions come from Azahar's loader
#define IMGUI_IMPL_VULKAN_NO_PROTOTYPES
#include "imgui_impl_vulkan.cpp"

#include <mutex>

#include "common/logging/log.h"
#include "ui/layer.h"
#include "video_core/renderer_vulkan/vk_overlay.h"

namespace App::Ui {

namespace {

struct VulkanState {
    bool ready = false;
    bool failed = false;
    PFN_vkGetInstanceProcAddr get_instance_proc_addr = nullptr;
    VkInstance instance = VK_NULL_HANDLE;
    std::mutex* queue_mutex = nullptr;
};
VulkanState vk; // with the layer's lock

PFN_vkVoidFunction LoadFunction(const char* name, void*) {
    return vk.get_instance_proc_addr(vk.instance, name);
}

void CheckResult(VkResult result) {
    if (result < 0) {
        LOG_ERROR(Frontend, "menu: Vulkan error {}", static_cast<int>(result));
    }
}

/// The backend set up for this renderer (with the lock, on the emulation thread).
bool Init(Layer& layer, const Vulkan::PresentOverlay::Context& c) {
    vk.get_instance_proc_addr = c.get_instance_proc_addr;
    vk.instance = c.instance;
    vk.queue_mutex = c.queue_mutex;
    if (!vk.get_instance_proc_addr || !ImGui_ImplVulkan_LoadFunctions(c.api_version, LoadFunction)) {
        LOG_ERROR(Frontend, "menu: Vulkan functions not found; no menu over Vulkan");
        return false;
    }
    ImGui_ImplVulkan_InitInfo info{};
    info.ApiVersion = c.api_version;
    info.Instance = c.instance;
    info.PhysicalDevice = c.physical_device;
    info.Device = c.device;
    info.QueueFamily = c.queue_family;
    info.Queue = c.queue;
    info.DescriptorPoolSize = IMGUI_IMPL_VULKAN_MINIMUM_SAMPLED_IMAGE_POOL_SIZE;
    // a set of vertex buffers per frame the renderer can have in flight
    info.MinImageCount = std::max<u32>(c.image_count, 2);
    info.ImageCount = info.MinImageCount;
    info.PipelineInfoMain.RenderPass = c.render_pass;
    info.PipelineInfoMain.Subpass = 0;
    info.PipelineInfoMain.MSAASamples = VK_SAMPLE_COUNT_1_BIT;
    info.CheckVkResultFn = CheckResult;
    if (!ImGui_ImplVulkan_Init(&info)) {
        LOG_ERROR(Frontend, "menu: no Vulkan renderer");
        return false;
    }
    layer.SetRendererReady(true);
    LOG_INFO(Frontend, "menu: drawn over Vulkan ({} frames in flight)", info.ImageCount);
    return true;
}

/// On the scheduler's thread, inside the present render pass: the menu's last frame.
void Record(Layer& layer, VkCommandBuffer cmdbuf, u32 width, u32 height) {
    auto lock = layer.Lock();
    if (!vk.ready) {
        return;
    }
    ImDrawData* data = layer.DrawData(width, height);
    if (!data) {
        return;
    }
    layer.MakeCurrent();
    // textures first: an upload is a submission of its own to the renderer's queue
    ImVector<ImTextureData*>* textures = data->Textures;
    if (textures) {
        bool uploads = false;
        for (const ImTextureData* tex : *textures) {
            uploads |= tex->Status == ImTextureStatus_WantCreate || tex->Status == ImTextureStatus_WantUpdates;
        }
        std::unique_lock queue_lock{*vk.queue_mutex, std::defer_lock};
        if (uploads) {
            queue_lock.lock();
        }
        for (ImTextureData* tex : *textures) {
            if (tex->Status != ImTextureStatus_OK) {
                ImGui_ImplVulkan_UpdateTexture(tex);
            }
        }
    }
    data->Textures = nullptr;
    ImGui_ImplVulkan_RenderDrawData(data, cmdbuf);
    data->Textures = textures;
}

} // namespace

void RegisterVulkanOverlay(Layer& layer) {
    Vulkan::PresentOverlay overlay;
    overlay.draw = [&layer](const Vulkan::PresentOverlay::Context& context, u32 width,
                            u32 height) -> Vulkan::PresentOverlay::Commands {
        {
            auto lock = layer.Lock();
            layer.MakeCurrent();
            if (!vk.ready && !vk.failed) {
                vk.ready = Init(layer, context);
                vk.failed = !vk.ready;
            }
            if (!vk.ready) {
                return {};
            }
        }
        if (!layer.Visible()) {
            return {};
        }
        return [&layer, width, height](VkCommandBuffer cmdbuf) { Record(layer, cmdbuf, width, height); };
    };
    overlay.release = [&layer] {
        // the renderer is going away, its device idle
        auto lock = layer.Lock();
        layer.MakeCurrent();
        if (vk.ready) {
            layer.SetRendererReady(false);
            ImGui_ImplVulkan_Shutdown();
        }
        vk = {};
    };
    Vulkan::SetPresentOverlay(std::move(overlay));
}

void UnregisterVulkanOverlay() {
    Vulkan::SetPresentOverlay({});
}

} // namespace App::Ui
