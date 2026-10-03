' Recorded by SAP GUI's Script Recording and Playback. UTF-16 transcoded to UTF-8. No personal values present (SE16N read of BUT000).

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
session.findById("wnd[0]/usr/ctxtGD-TAB").text = "BUT000"
session.findById("wnd[0]/usr/ctxtGD-TAB").caretPosition = 6
session.findById("wnd[0]").sendVKey 0
session.findById("wnd[0]").sendVKey 8
session.findById("wnd[0]/shellcont/shell").setCurrentCell 2,"BU_SORT1"
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 1
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 8
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 64
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 73
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 97
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 111
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 119
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 135
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 143
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 190
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 196
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 210
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 216
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 301
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 322
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 326
session.findById("wnd[0]/shellcont/shell").firstVisibleRow = 412
