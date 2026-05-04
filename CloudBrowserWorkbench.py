# FreeCAD Cloud Browser - CloudBrowserWorkbench.py
# Main workbench module: commands, toolbar actions

import FreeCADGui


class CommandOpenCloudBrowser:
    """Command to open the Cloud Browser panel."""

    def GetResources(self):
        return {
            "Pixmap": "",  # No icon for menu commands, workbench icon is enough
            "MenuText": "Open Cloud Browser",
            "ToolTip": "Browse and open files from cloud storage",
            "Accel": "Ctrl+Shift+C",
        }

    def Activated(self):
        try:
            from ui.browser_panel import CloudBrowserDialog
            dlg = CloudBrowserDialog()
            dlg.exec()
        except Exception as e:
            from PySide6 import QtWidgets
            QtWidgets.QMessageBox.critical(None, "Cloud Browser Error", str(e))

    def IsActive(self):
        return True


class CommandAddProvider:
    """Command to add a new cloud storage provider."""

    def GetResources(self):
        return {
            "MenuText": "Add Cloud Provider",
            "ToolTip": "Add a new cloud storage provider (Google Drive, S3, FTP, etc.)",
        }

    def Activated(self):
        try:
            from ui.provider_dialog import ProviderDialog
            dialog = ProviderDialog()
            dialog.exec()
        except Exception as e:
            from PySide6 import QtWidgets
            QtWidgets.QMessageBox.critical(None, "Cloud Browser Error", str(e))

    def IsActive(self):
        return True


class CommandManageProviders:
    """Command to manage existing cloud storage providers."""

    def GetResources(self):
        return {
            "MenuText": "Manage Cloud Providers",
            "ToolTip": "View, edit and remove configured cloud storage providers",
        }

    def Activated(self):
        try:
            from ui.provider_dialog import ManageProvidersDialog
            dialog = ManageProvidersDialog()
            dialog.exec()
        except Exception as e:
            from PySide6 import QtWidgets
            QtWidgets.QMessageBox.critical(None, "Cloud Browser Error", str(e))

    def IsActive(self):
        return True


def register_commands():
    """
    Register all Cloud Browser commands with FreeCAD.

    NOTABLE-1 fix: previously these addCommand() calls lived at module level,
    so they ran as a side-effect of the first import of this module (which
    happened inside Initialize()).  Moving them into an explicit function makes
    the registration intentional and testable, and avoids surprising behaviour
    if the module is imported in other contexts.
    """
    FreeCADGui.addCommand("CloudBrowser_Open", CommandOpenCloudBrowser())
    FreeCADGui.addCommand("CloudBrowser_AddProvider", CommandAddProvider())
    FreeCADGui.addCommand("CloudBrowser_ManageProviders", CommandManageProviders())
