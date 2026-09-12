# FreeCAD Git Integration - GUI initialization
# Registers the workbench with FreeCADGui.

import os

import FreeCAD  # type: ignore[import-not-found]
import FreeCADGui  # type: ignore[import-not-found]


FreeCAD.Console.PrintMessage("git startup: InitGui loaded (freecad-git)\n")


class GitWorkbench(FreeCADGui.Workbench):
    MenuText = "Git"
    ToolTip = "Selective git versioning for FreeCAD documents"

    # Icon: try to load from the icons directory
    try:
        import freecad_git as _fcgit
        Icon = os.path.join(os.path.dirname(_fcgit.__file__), "icons", "workbench.svg")
    except Exception:
        Icon = ""

    def Initialize(self):
        from freecad_git import commands  # noqa: F401 -- registers GUI commands
        toolbar_cmds = ["Git_Commit", "Git_Pull", "Git_Log"]
        menu_cmds = ["Git_Commit", "Git_Pull", "Git_Log", "Git_ToggleAutoStart"]
        self.appendToolbar("Git", toolbar_cmds)
        self.appendMenu("Git", menu_cmds)

    def Activated(self):
        return

    def Deactivated(self):
        return

    def GetClassName(self):
        return "Gui::PythonWorkbench"


def _activate_git_on_startup_if_enabled():
    try:
        from PySide6 import QtCore  # type: ignore[import-not-found]

        prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/freecad-git")
        enabled = prefs.GetBool("AutoStartWorkbench", False)
        FreeCAD.Console.PrintMessage(f"git startup: AutoStartWorkbench={enabled}\n")
        if not enabled:
            return

        activation_done = {"done": False}

        def _activate():
            if activation_done["done"]:
                return

            try:
                workbenches = FreeCADGui.listWorkbenches()
            except Exception:
                workbenches = {}

            wb_name = "GitWorkbench" if "GitWorkbench" in workbenches else None
            if wb_name is None:
                for name, wb in workbenches.items():
                    if getattr(wb, "MenuText", "") == "Git":
                        wb_name = name
                        break

            FreeCAD.Console.PrintMessage(f"git startup: resolved workbench={wb_name}\n")
            if not wb_name:
                return

            try:
                FreeCADGui.activateWorkbench(wb_name)
                activation_done["done"] = True
                FreeCAD.Console.PrintMessage(f"git startup: activated {wb_name}\n")
            except Exception as exc:
                FreeCAD.Console.PrintMessage(f"git startup: activation failed for {wb_name}: {exc}\n")

        for delay_ms in (250, 1000, 2500, 5000, 8000):
            QtCore.QTimer.singleShot(delay_ms, _activate)
    except Exception as exc:
        FreeCAD.Console.PrintMessage(f"git startup: init exception: {exc}\n")


FreeCADGui.addWorkbench(GitWorkbench())
_activate_git_on_startup_if_enabled()
