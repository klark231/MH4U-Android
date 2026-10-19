// Export Ghidra's analysis as plain text for MH4UltimateDC agents.
//
// Writes to the chosen folder (keep it OUTSIDE any git repo, it is derived from game code):
//   meta.txt        program/language info and counts
//   functions.tsv   address, size, arm/thumb, name, #callers, #callees, thunk flag
//   callgraph.tsv   caller -> callee edges
//   strings.tsv     defined strings with the functions that reference them
//   decomp/<addr>.c Ghidra pseudo-C (optional: all functions, or only those listed in a file)
//
// GUI: Script Manager -> run, answer the prompts.
// Headless: analyzeHeadless <proj_dir> <proj_name> -process <program> -noanalysis \
//             -scriptPath <dir> -postScript ExportForAgents.java <out_dir> [none|all|<address_list.txt>]
//
//@category MH4UltimateDC
//@author MH4UltimateDC contributors

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.DataIterator;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.symbol.Reference;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

public class ExportForAgents extends GhidraScript {

    private static final int DECOMPILE_TIMEOUT_SECONDS = 60;

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        File outDir;
        String mode;
        if (args.length > 0) {
            outDir = new File(args[0]);
            mode = args.length > 1 ? args[1] : "none";
        } else {
            outDir = askDirectory("Export folder (outside any git repo)", "Export here");
            List<String> choices = Arrays.asList(
                "none (fast: metadata only)",
                "from address list file",
                "all functions (slow: hours)");
            String c = askChoice("Pseudo-C", "Export Ghidra pseudo-C?", choices, choices.get(0));
            if (c.startsWith("all")) {
                mode = "all";
            } else if (c.startsWith("from")) {
                mode = askFile("Address list (one hex address per line)", "Use list").getAbsolutePath();
            } else {
                mode = "none";
            }
        }
        if (!outDir.isDirectory() && !outDir.mkdirs()) {
            throw new IllegalArgumentException("cannot create " + outDir);
        }

        FunctionManager fm = currentProgram.getFunctionManager();
        int funcCount = exportFunctions(outDir, fm);
        int strCount = exportStrings(outDir, fm);
        int decompiled = 0;
        if (!mode.equals("none")) {
            decompiled = exportPseudoC(outDir, fm, mode);
        }

        try (PrintWriter meta = writer(outDir, "meta.txt")) {
            meta.println("program\t" + currentProgram.getName());
            meta.println("language\t" + currentProgram.getLanguageID());
            meta.println("compiler\t" + currentProgram.getCompilerSpec().getCompilerSpecID());
            meta.println("image_base\t" + currentProgram.getImageBase());
            meta.println("functions\t" + funcCount);
            meta.println("strings\t" + strCount);
            meta.println("pseudo_c\t" + decompiled);
            meta.println("pseudo_c_mode\t" + mode);
        }
        println(String.format("Exported %d functions, %d strings, %d pseudo-C files to %s",
            funcCount, strCount, decompiled, outDir));
    }

    private int exportFunctions(File outDir, FunctionManager fm) throws Exception {
        Register tmode = currentProgram.getRegister("TMode");
        monitor.setMessage("Exporting functions and call graph");
        monitor.initialize(fm.getFunctionCount());
        int n = 0;
        try (PrintWriter funcs = writer(outDir, "functions.tsv");
             PrintWriter calls = writer(outDir, "callgraph.tsv")) {
            funcs.println("address\tsize\tmode\tname\tcallers\tcallees\tthunk");
            calls.println("caller\tcallee");
            for (Function f : fm.getFunctions(true)) {
                monitor.checkCancelled();
                monitor.incrementProgress(1);
                Address ep = f.getEntryPoint();
                String isa = "arm";
                if (tmode != null) {
                    BigInteger v = currentProgram.getProgramContext().getValue(tmode, ep, false);
                    if (v != null && v.intValue() == 1) {
                        isa = "thumb";
                    }
                }
                Set<Function> callees = f.getCalledFunctions(monitor);
                Set<Function> callers = f.getCallingFunctions(monitor);
                funcs.printf("0x%08x\t0x%x\t%s\t%s\t%d\t%d\t%d%n", ep.getOffset(),
                    f.getBody().getNumAddresses(), isa, clean(f.getName(true)),
                    callers.size(), callees.size(), f.isThunk() ? 1 : 0);
                for (Function c : callees) {
                    calls.printf("0x%08x\t0x%08x%n", ep.getOffset(), c.getEntryPoint().getOffset());
                }
                n++;
            }
        }
        return n;
    }

    private int exportStrings(File outDir, FunctionManager fm) throws Exception {
        monitor.setMessage("Exporting strings");
        int n = 0;
        try (PrintWriter strs = writer(outDir, "strings.tsv")) {
            strs.println("address\tref_functions\tvalue");
            DataIterator it = currentProgram.getListing().getDefinedData(true);
            while (it.hasNext()) {
                monitor.checkCancelled();
                Data d = it.next();
                if (!d.hasStringValue() || d.getValue() == null) {
                    continue;
                }
                Set<String> refs = new LinkedHashSet<>();
                for (Reference r : getReferencesTo(d.getAddress())) {
                    Function rf = fm.getFunctionContaining(r.getFromAddress());
                    // ARM code reaches strings through literal pools, which may sit just
                    // outside a function body; fall back to the raw referencing address.
                    refs.add(rf != null
                        ? String.format("0x%08x", rf.getEntryPoint().getOffset())
                        : String.format("@0x%08x", r.getFromAddress().getOffset()));
                }
                strs.printf("0x%08x\t%s\t%s%n", d.getAddress().getOffset(),
                    String.join(",", refs), clean(d.getValue().toString()));
                n++;
            }
        }
        return n;
    }

    private int exportPseudoC(File outDir, FunctionManager fm, String mode) throws Exception {
        List<Function> targets = new ArrayList<>();
        if (mode.equals("all")) {
            for (Function f : fm.getFunctions(true)) {
                if (!f.isThunk() && !f.isExternal()) {
                    targets.add(f);
                }
            }
        } else {
            for (String line : Files.readAllLines(new File(mode).toPath(), StandardCharsets.UTF_8)) {
                line = line.trim();
                if (line.isEmpty() || line.startsWith("#")) {
                    continue;
                }
                Address a = toAddr(Long.decode(line.split("\\s+")[0]));
                Function f = fm.getFunctionAt(a);
                if (f == null) {
                    printerr("no function at " + line);
                } else {
                    targets.add(f);
                }
            }
        }

        File dir = new File(outDir, "decomp");
        dir.mkdirs();
        DecompInterface di = new DecompInterface();
        di.toggleCCode(true);
        di.toggleSyntaxTree(false);
        di.setSimplificationStyle("decompile");
        if (!di.openProgram(currentProgram)) {
            throw new IllegalStateException("decompiler failed to start: " + di.getLastMessage());
        }
        monitor.setMessage("Decompiling");
        monitor.initialize(targets.size());
        int ok = 0;
        try {
            for (Function f : targets) {
                monitor.checkCancelled();
                monitor.incrementProgress(1);
                DecompileResults r = di.decompileFunction(f, DECOMPILE_TIMEOUT_SECONDS, monitor);
                String body;
                if (r != null && r.decompileCompleted() && r.getDecompiledFunction() != null) {
                    body = r.getDecompiledFunction().getC();
                    ok++;
                } else {
                    body = "/* decompile failed: " + (r == null ? "no result" : r.getErrorMessage()) + " */\n";
                }
                String name = String.format("%08x.c", f.getEntryPoint().getOffset());
                Files.write(new File(dir, name).toPath(), body.getBytes(StandardCharsets.UTF_8));
            }
        } finally {
            di.dispose();
        }
        return ok;
    }

    private static String clean(String s) {
        return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n");
    }

    private static PrintWriter writer(File dir, String name) throws Exception {
        return new PrintWriter(new BufferedWriter(new OutputStreamWriter(
            new FileOutputStream(new File(dir, name)), StandardCharsets.UTF_8)));
    }
}
