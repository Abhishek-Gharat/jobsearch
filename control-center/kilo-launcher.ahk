#Requires AutoHotkey v2.0
#SingleInstance Force

; ============================================================
; Kilo Launcher for Job Control Center
; Modes:
;   detect - only detect Kilo
;   run    - create new Kilo task and send prompt
; ============================================================

SetWorkingDir A_ScriptDir

mode := (A_Args.Length >= 1 && A_Args[1] != "") ? A_Args[1] : "run"
curFile := (A_Args.Length >= 2 && A_Args[2] != "") ? A_Args[2] : A_ScriptDir "\data\current-task.json"
resultFile := (A_Args.Length >= 3 && A_Args[3] != "") ? A_Args[3] : A_ScriptDir "\data\launcher-result.json"
cfgFile := (A_Args.Length >= 4 && A_Args[4] != "") ? A_Args[4] : A_ScriptDir "\data\kilo-config.json"

cfg := ReadConfig(cfgFile)
winId := FindKiloWindow(cfg)

if !winId
{
    WriteResult(0,0,"Kilo window not found",mode,resultFile)
    ExitApp 1
}

if (mode = "detect")
{
    WriteResult(1,1,"",mode,resultFile)
    ExitApp 0
}

; ------------------------------------------------------------
; Read current task
; ------------------------------------------------------------

content := FileExist(curFile) ? FileRead(curFile,"UTF-8") : ""

idPos := RegExMatch(content,'"id"\s*:\s*(\d+)',&idMatch)
taskId := idPos ? Number(idMatch[1]) : 0

promptPos := RegExMatch(content,'"prompt"\s*:\s*"((?:[^"\\]|\\.)*)"',&promptMatch)
prompt := promptPos ? Unescape(promptMatch[1]) : ""

if (prompt = "")
{
    WriteResult(0,1,"No prompt in current-task.json",mode,resultFile)
    ExitApp 1
}

; ------------------------------------------------------------
; Activate VS Code / Kilo
; ------------------------------------------------------------

WinRestore winId
WinActivate winId

if !WinWaitActive(winId,,3)
{
    WriteResult(0,1,"Failed to activate Kilo",mode,resultFile)
    ExitApp 1
}

Sleep 400

; ------------------------------------------------------------
; Kilo: New Task
; ------------------------------------------------------------

Send "^+p"
Sleep 400

A_Clipboard := ">Kilo Code: New Task"

if !ClipWait(2)
{
    WriteResult(0,1,"Clipboard timeout",mode,resultFile)
    ExitApp 1
}

Send "^v"
Sleep 200
Send "{Enter}"

Sleep 800

; ------------------------------------------------------------
; Focus Chat Input
; ------------------------------------------------------------

Send "^+p"
Sleep 300

A_Clipboard := ">Kilo Code: Focus Chat Input"

if !ClipWait(2)
{
    WriteResult(0,1,"Clipboard timeout",mode,resultFile)
    ExitApp 1
}

Send "^v"
Sleep 200
Send "{Enter}"

Sleep 700

; ------------------------------------------------------------
; Send Prompt
; ------------------------------------------------------------

A_Clipboard := prompt

if !ClipWait(2)
{
    WriteResult(0,1,"Clipboard timeout",mode,resultFile)
    ExitApp 1
}

Send "^v"
Sleep 250
Send "{Enter}"

WriteResult(1,1,"",mode,resultFile)
ExitApp 0

; ============================================================
; Helpers
; ============================================================

Unescape(s)
{
    q := Chr(34)
    bs := Chr(92)

    s := StrReplace(s,bs q,q)
    s := StrReplace(s,bs "n","`n")
    s := StrReplace(s,bs "t","`t")
    s := StrReplace(s,bs "r","`r")
    s := StrReplace(s,bs bs,bs)

    return s
}

ReasonEsc(s)
{
    q := Chr(34)
    bs := Chr(92)

    s := StrReplace(s,bs,bs bs)
    s := StrReplace(s,q,bs q)

    return s
}

WriteResult(success,kilo,reason,mode,resultFile)
{
    q := Chr(34)

    json := "{"
    json .= q "success" q ":" success
    json .= "," q "kiloDetected" q ":" kilo
    json .= "," q "mode" q ":" q mode q
    json .= "," q "at" q ":" q FormatTime(,"yyyy-MM-dd HH:mm:ss") q
    json .= "," q "reason" q ":" q ReasonEsc(reason) q
    json .= "}"

    try
    {
        if FileExist(resultFile)
            FileDelete resultFile
    }

    FileAppend(json,resultFile,"UTF-8")
}

IsMatch(text,patterns)
{
    for p in patterns
        if InStr(text,p,true)
            return true
    return false
}

FindKiloWindow(cfg)
{
    try
    {
        active := WinGetID("A")

        if IsMatch(WinGetProcessName(active),cfg.processNames)
            return active

        if IsMatch(WinGetTitle(active),cfg.titlePatterns)
            return active
    }

    for hwnd in WinGetList()
    {
        try
        {
            if IsMatch(WinGetProcessName(hwnd),cfg.processNames)
                return hwnd
        }
    }

    for hwnd in WinGetList()
    {
        try
        {
            if IsMatch(WinGetTitle(hwnd),cfg.titlePatterns)
                return hwnd
        }
    }

    if cfg.vsCodeFallback
    {
        for hwnd in WinGetList()
        {
            try
            {
                if IsMatch(WinGetProcessName(hwnd),cfg.vsCodeProcesses)
                    return hwnd
            }
        }
    }

    return 0
}

ReadConfig(file)
{
    cfg := {
        processNames:["kilo"],
        titlePatterns:["kilo"],
        vsCodeFallback:true,
        vsCodeProcesses:[
            "Code.exe",
            "Code - Insiders.exe",
            "VSCodium.exe",
            "Cursor.exe",
            "Windsurf.exe"
        ]
    }

    if !FileExist(file)
        return cfg

    txt := FileRead(file,"UTF-8")

    if RegExMatch(txt,'"processNames"\s*:\s*\[(.*?)\]',&m)
    {
        arr := []
        for item in StrSplit(m[1],",")
        {
            s := Trim(Trim(item),Chr(34))
            if s != ""
                arr.Push(s)
        }
        if arr.Length
            cfg.processNames := arr
    }

    if RegExMatch(txt,'"titlePatterns"\s*:\s*\[(.*?)\]',&m)
    {
        arr := []
        for item in StrSplit(m[1],",")
        {
            s := Trim(Trim(item),Chr(34))
            if s != ""
                arr.Push(s)
        }
        if arr.Length
            cfg.titlePatterns := arr
    }

    if RegExMatch(txt,'"vsCodeFallback"\s*:\s*(true|false)',&m)
        cfg.vsCodeFallback := (m[1] = "true")

    if RegExMatch(txt,'"vsCodeProcesses"\s*:\s*\[(.*?)\]',&m)
    {
        arr := []
        for item in StrSplit(m[1],",")
        {
            s := Trim(Trim(item),Chr(34))
            if s != ""
                arr.Push(s)
        }
        if arr.Length
            cfg.vsCodeProcesses := arr
    }

    return cfg
}