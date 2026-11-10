# FreeCAD Git Integration - GUI initialization
# Registers the workbench with FreeCADGui.

import os

import FreeCAD  # type: ignore[import-not-found]
import FreeCADGui  # type: ignore[import-not-found]


FreeCAD.Console.PrintMessage("git startup: freecad-git addon initialized (not a workbench activation signal)\n")


def _register_translations():
    module_file = globals().get("__file__")
    if module_file:
        root_dir = os.path.dirname(os.path.abspath(module_file))
    else:
        try:
            import freecad_git as _fcgit
            root_dir = os.path.dirname(os.path.dirname(os.path.abspath(_fcgit.__file__)))
        except Exception:
            root_dir = os.getcwd()

    translations_dir = os.path.join(root_dir, "freecad_git", "translations")
    if not os.path.isdir(translations_dir):
        return
    if hasattr(FreeCADGui, "addLanguagePath"):
        FreeCADGui.addLanguagePath(translations_dir)
    if hasattr(FreeCADGui, "updateLocale"):
        FreeCADGui.updateLocale()


class GitWorkbench(FreeCADGui.Workbench):
    # Keep these as plain strings at class definition time so the workbench can
    # still be created even if translation hooks are not yet ready.
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


_register_translations()


def _register_preferences():
    try:
        if not hasattr(FreeCADGui, "addPreferencePage"):
            return
        module_file = globals().get("__file__")
        if module_file:
            root_dir = os.path.dirname(os.path.abspath(module_file))
        else:
            try:
                import freecad_git as _fcgit
                root_dir = os.path.dirname(os.path.dirname(os.path.abspath(_fcgit.__file__)))
            except Exception:
                root_dir = os.getcwd()
        ui_file = os.path.join(root_dir, "freecad_git", "preferences.ui")
        if os.path.isfile(ui_file):
            # Category label in FreeCAD's Preferences dialog.
            FreeCADGui.addPreferencePage(ui_file, "Git")
            FreeCAD.Console.PrintMessage(f"git startup: preference page registered from {ui_file}\n")
        else:
            FreeCAD.Console.PrintMessage(f"git startup: preference page not found: {ui_file}\n")
    except Exception as exc:
        FreeCAD.Console.PrintMessage(f"git startup: preference page registration failed: {exc}\n")


def _ensure_default_preferences():
    """Materialize default keys so they are visible in FreeCAD Parameter Editor."""
    try:
        prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/freecad-git")
        default_colors = {
            "SelectedCommitColor": "#6496C8",
            "CurrentCommitColor": "#78B478",
            "AncestorCommitColor": "#DCC064",
        }
        for key, value in default_colors.items():
            if not prefs.GetString(key, "").strip():
                prefs.SetString(key, value)
    except Exception as exc:
        FreeCAD.Console.PrintMessage(f"git startup: default preference init failed: {exc}\n")


def _migrate_legacy_autostart():
    """One-time move of the legacy AutoStartWorkbench flag to FreeCAD's startup preferences."""
    try:
        prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/freecad-git")
        if "AutoStartWorkbench" not in prefs.GetBools():
            return
        legacy_enabled = prefs.GetBool("AutoStartWorkbench", False)
        prefs.RemBool("AutoStartWorkbench")
        if not legacy_enabled:
            return

        git_ids = ("Git", "GitWorkbench")
        wb_prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Workbenches")
        general_prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/General")
        default_wb = wb_prefs.GetString("DefaultWorkbench", "").strip()
        autoload = general_prefs.GetString("AutoloadModule", "").strip()
        if default_wb in git_ids or autoload in git_ids:
            return

        # Keep the previous startup module so disabling auto-start can restore it.
        previous = default_wb or autoload
        if previous:
            prefs.SetString("PreviousAutoloadModule", previous)
        wb_prefs.SetString("DefaultWorkbench", "GitWorkbench")
        general_prefs.SetString("AutoloadModule", "GitWorkbench")
        FreeCAD.Console.PrintMessage("git startup: migrated legacy AutoStartWorkbench to startup preferences\n")
    except Exception as exc:
        FreeCAD.Console.PrintMessage(f"git startup: legacy auto-start migration failed: {exc}\n")


_ensure_default_preferences()
_migrate_legacy_autostart()
_register_preferences()
FreeCADGui.addWorkbench(GitWorkbench())
