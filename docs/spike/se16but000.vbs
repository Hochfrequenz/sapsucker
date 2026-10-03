' Recorded by SAP GUI's Script Recording and Playback. UTF-16 transcoded to UTF-8. No personal values present (SE16N read of BUT000, scrolled).

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
session.findById("wnd[0]/usr/ctxtGD-TAB").text = "but000"
session.findById("wnd[0]").sendVKey 0
session.findById("wnd[0]").sendVKey 8
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 1
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 8
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 17
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 26
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 35
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 44
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 70
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 78
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 133
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 470
session.findById("wnd[0]/shellcont/shell").setCurrentCell 497,"BU_SORT1"
