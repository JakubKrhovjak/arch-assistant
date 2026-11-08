"""The Qt user interface: windows, dialogs and widgets. Widgets live only in this package and in tray.py (notify.py
uses QtCore signals to hand D-Bus events to the GUI thread); everything that reads the system stays Qt-free."""
