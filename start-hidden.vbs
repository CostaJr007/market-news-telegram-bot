Option Explicit

Dim fso, shell, projectDir, command
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

projectDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = projectDir

' pythonw.exe executa sem janela de console. Os logs vao para bot.log.
command = "pythonw.exe """ & projectDir & "\bot.py"""
shell.Run command, 0, False
