# FreeCAD Cloud Browser Plugin - InitGui.py
# This file is loaded by FreeCAD on startup to register the workbench

import os
import sys
import FreeCAD
import FreeCADGui


class CloudBrowserWorkbench(FreeCADGui.Workbench):
    """FreeCAD Cloud Browser Workbench - Browse and open files from cloud storage."""

    MenuText = "Cloud Browser"
    ToolTip = "Browse and open files from cloud storage providers"
    Icon = ""

    def __init__(self):
        super().__init__()
        # Find plugin dir from sys.path (FreeCAD adds the Mod subdir to sys.path)
        icon_rel = os.path.join("resources", "icons", "cloud_browser.svg")
        for path in sys.path:
            candidate = os.path.join(path, icon_rel)
            if os.path.isfile(candidate):
                self.Icon = candidate
                break
        else:
            # Fallback: search standard Mod directories.
            # Use a helper function so that returning from the inner loop
            # also exits the outer loop (a plain break would not).
            def _find_icon():
                for mod_root in [
                    os.path.join(FreeCAD.getUserAppDataDir(), "Mod"),
                    os.path.join(FreeCAD.getResourceDir(), "Mod"),
                ]:
                    for name in ("freecad-cloud-browser", "CloudBrowser"):
                        candidate = os.path.join(mod_root, name, icon_rel)
                        if os.path.isfile(candidate):
                            return candidate
                return ""
            self.Icon = _find_icon()

    def Initialize(self):
        """Called when the workbench is first activated."""
        from CloudBrowserWorkbench import register_commands
        register_commands()

        self.appendToolbar("Cloud Browser", [
            "CloudBrowser_Open",
            "CloudBrowser_AddProvider",
            "CloudBrowser_ManageProviders",
        ])
        self.appendMenu("Cloud Browser", [
            "CloudBrowser_Open",
            "Separator",
            "CloudBrowser_AddProvider",
            "CloudBrowser_ManageProviders",
        ])

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(CloudBrowserWorkbench())
