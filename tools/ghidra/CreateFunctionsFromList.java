// Create functions at addresses listed in a text file (one hex address per line,
// anything after whitespace is ignored, '#' starts a comment), then let Ghidra's
// auto-analysis pick them up. Input comes from tools/find_missing_functions.py.
//
//@category MH4UltimateDC
//@author MH4UltimateDC contributors

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

public class CreateFunctionsFromList extends GhidraScript {

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        File list = args.length > 0 ? new File(args[0])
            : askFile("Address list (missing_functions.txt)", "Create functions");

        int created = 0, existed = 0, failed = 0;
        java.util.List<String> lines = Files.readAllLines(list.toPath(), StandardCharsets.UTF_8);
        monitor.initialize(lines.size());
        for (String line : lines) {
            monitor.checkCancelled();
            monitor.incrementProgress(1);
            line = line.trim();
            if (line.isEmpty() || line.startsWith("#")) {
                continue;
            }
            Address a = toAddr(Long.decode(line.split("\\s+")[0]));
            if (getFunctionAt(a) != null) {
                existed++;
                continue;
            }
            if (getInstructionAt(a) == null) {
                disassemble(a);
            }
            Function f = createFunction(a, null);
            if (f != null) {
                created++;
            } else {
                failed++;
            }
        }
        println(String.format("Created %d functions (%d already existed, %d failed)", created, existed, failed));
        println("Now run Analysis -> Auto Analyze again so callers/callees of the new functions are found.");
    }
}
