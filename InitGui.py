# FreeCAD Git Integration - GUI initialization
# Registers the workbench with FreeCADGui.

import os
import FreeCADGui


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
        cmds = ["Git_Commit", "Git_Pull", "Git_Log"]
        self.appendToolbar("Git", cmds)
        self.appendMenu("Git", cmds)

    def Activated(self):
        return

    def Deactivated(self):
        return

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(GitWorkbench())
