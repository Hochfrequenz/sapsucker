' Recorded by SAP GUI's Script Recording and Playback. UTF-16 transcoded to UTF-8. No personal values present (SE16N read of T000).

If Not IsObject(application) Then
   Set SapGuiAuto  = GetObject("SAPGUI")
   Set application = SapGuiAuto.GetScriptingEngine
End If
If Not IsObject(connection) Then
   Set connection = application.Children(0)
End If
If Not IsObject(session) Then
   Set session    = connection.Children(0)
End If
If IsObject(WScript) Then
   WScript.ConnectObject session,     "on"
   WScript.ConnectObject application, "on"
End If
session.findById("wnd[0]").resizeWorkingPane 152,33,false
session.findById("wnd[0]/tbar[0]/okcd").text = "/nse16n"
session.findById("wnd[0]").sendVKey 0
session.findById("wnd[0]/usr/ctxtGD-TAB").text = "T000"
session.findById("wnd[0]/usr/ctxtGD-TAB").caretPosition = 4
session.findById("wnd[0]").sendVKey 0
session.findById("wnd[0]").sendVKey 8
session.findById("wnd[0]/shellcont/shell").setCurrentCell 2,"MWAER"
session.findById("wnd[0]/shellcont/shell").pressF4
session.findById("wnd[1]/usr/lbl[7,9]").setFocus
session.findById("wnd[1]/usr/lbl[7,9]").caretPosition = 22
session.findById("wnd[1]").sendVKey 12
session.findById("wnd[0]/shellcont/shell").currentCellColumn = "LOGSYS"
