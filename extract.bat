@echo off
setlocal enabledelayedexpansion
title CD PAM/PAMLOD Extractor v3.2
chcp 65001 >nul 2>&1

:: ============================================================
::  Startup checks
:: ============================================================
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [ERROR] Python not found in PATH.
    echo.
    pause & exit /b 1
)

set "SCRIPT_DIR=%~dp0"
set "EXTRACTOR=%SCRIPT_DIR%cd_extractor.py"
set PYTHONIOENCODING=utf-8

if not exist "%EXTRACTOR%" (
    echo.
    echo  [ERROR] cd_extractor.py not found: %EXTRACTOR%
    echo.
    pause & exit /b 1
)

:: ============================================================
::  Drag-and-drop
:: ============================================================
if not "%~1"=="" (
    if exist "%~1\." (
        set "BATCH_DIR=%~1"
        goto :FMT_BATCH
    )
    set "PAM_FILE=%~1"
    goto :FMT_SINGLE
)

:: ============================================================
::  Main menu
:: ============================================================
:MAIN
cls
echo.
echo  +------------------------------------------+
echo  ^|  Crimson Desert PAM/PAMLOD Extractor     ^|
echo  ^|  v3.2                                    ^|
echo  +------------------------------------------+
echo.
echo    1 - Extract a single PAM file
echo    2 - Extract all PAM files in a folder
echo    3 - Exit
echo.
choice /c 123 /n /m "  Choose [1-3]: "
if errorlevel 3 exit /b 0
if errorlevel 2 goto :PICK_FOLDER
if errorlevel 1 goto :PICK_FILE

:: ============================================================
::  GUI pickers
:: ============================================================
:PICK_FILE
set "PAM_FILE="
for /f "usebackq delims=" %%i in (`powershell -noprofile -executionpolicy bypass -file "%SCRIPT_DIR%pick.ps1" file`) do set "PAM_FILE=%%i"
if "!PAM_FILE!"=="" goto :MAIN
if "!PAM_FILE!"=="CANCELLED" goto :MAIN
goto :FMT_SINGLE

:PICK_FOLDER
set "BATCH_DIR="
for /f "usebackq delims=" %%i in (`powershell -noprofile -executionpolicy bypass -file "%SCRIPT_DIR%pick.ps1" folder`) do set "BATCH_DIR=%%i"
if "!BATCH_DIR!"=="" goto :MAIN
if "!BATCH_DIR!"=="CANCELLED" goto :MAIN
goto :FMT_BATCH

:: ============================================================
::  Single PAM - choose format + mode
:: ============================================================
:FMT_SINGLE
cls
echo.
echo  +------------------------------------------+
echo  ^|  Crimson Desert PAM/PAMLOD Extractor     ^|
echo  ^|  v3.2                                    ^|
echo  +------------------------------------------+
echo.
echo    File:   !PAM_FILE!
echo.
echo    Format:
echo      1 - FBX  (Binary FBX 7.4)
echo      2 - OBJ  (Wavefront OBJ + MTL)
echo.
choice /c 12 /n /m "  Choose [1-2]: "
if errorlevel 2 ( set "FORMAT=obj" ) else ( set "FORMAT=fbx" )

cls
echo.
echo  +------------------------------------------+
echo  ^|  Crimson Desert PAM/PAMLOD Extractor     ^|
echo  ^|  v3.2                                    ^|
echo  +------------------------------------------+
echo.
echo    File:   !PAM_FILE!
echo    Format: !FORMAT!
echo.
echo    Mode:
echo      1 - Combined   (all submeshes in one file)
echo      2 - Split      (one file per submesh)
echo      3 - Info only  (print mesh info, no export)
echo.
choice /c 123 /n /m "  Choose [1-3]: "
if errorlevel 3 ( set "EMODE=--info-only" ) else if errorlevel 2 ( set "EMODE=--split" ) else ( set "EMODE=" )

:: ============================================================
::  RUN SINGLE  (step-by-step: PAM -> LOD -> DDS -> open?)
:: ============================================================
:RUN_SINGLE
for %%F in ("!PAM_FILE!") do (
    set "PAM_STEM=%%~nF"
    set "PAM_DIR=%%~dpF"
    set "OUT_DIR=%%~dpF%%~nF"
)

cls
echo.
echo  +------------------------------------------+
echo  ^|  Crimson Desert PAM/PAMLOD Extractor     ^|
echo  ^|  v3.2                                    ^|
echo  +------------------------------------------+
echo.
echo  File   : !PAM_FILE!
echo  Output : !OUT_DIR!\
echo.
echo  ------------------------------------------

:: -------------------------------------------------------
::  STEP 1 - Extract PAM
:: -------------------------------------------------------
echo.
echo  [1/3] PAM  -- !PAM_STEM!.pam
echo.
python "!EXTRACTOR!" "!PAM_FILE!" --format !FORMAT! -o "!OUT_DIR!" !EMODE!
if errorlevel 1 (
    echo.
    echo  [1/3] FAILED  (see error above)
) else (
    echo.
    echo  [1/3] OK
)

if "!EMODE!"=="--info-only" goto :OPEN_PROMPT

:: -------------------------------------------------------
::  STEP 2 - Auto PAMLOD
:: -------------------------------------------------------
echo.
set "LOD_FILE=!PAM_DIR!!PAM_STEM!.pamlod"
if exist "!LOD_FILE!" (
    echo  [2/3] LOD  -- !PAM_STEM!.pamlod
    echo.
    python "!EXTRACTOR!" "!LOD_FILE!" --format !FORMAT! -o "!OUT_DIR!"
    if errorlevel 1 (
        echo.
        echo  [2/3] FAILED  (see error above)
    ) else (
        echo.
        echo  [2/3] OK
    )
) else (
    echo  [2/3] LOD  -- not found, skipping
)

:: -------------------------------------------------------
::  STEP 3 - Copy matching DDS textures
:: -------------------------------------------------------
echo.
echo  [3/3] DDS textures...
set "DDS_COUNT=0"
for /f "usebackq delims=" %%D in (`powershell -noprofile -executionpolicy bypass -file "%SCRIPT_DIR%pick.ps1" dds "!PAM_STEM!" "!PAM_DIR:~0,-1!"`) do (
    if !DDS_COUNT!==0 (
        if not exist "!OUT_DIR!" mkdir "!OUT_DIR!"
        echo.
    )
    copy /y "%%D" "!OUT_DIR!\" >nul 2>&1
    echo         copied: %%~nxD
    set /a DDS_COUNT+=1
)
if !DDS_COUNT!==0 (
    echo         none found, skipping
) else (
    echo.
    echo  [3/3] OK  (!DDS_COUNT! file(s) copied)
)

:: -------------------------------------------------------
::  DONE
:: -------------------------------------------------------
:OPEN_PROMPT
echo.
echo  ------------------------------------------
echo.
if "!EMODE!"=="--info-only" goto :SKIP_OPEN
choice /c YN /n /m "  Open output folder? [Y/N]: "
if errorlevel 2 goto :SKIP_OPEN
if exist "!OUT_DIR!" ( start "" explorer "!OUT_DIR!" )
:SKIP_OPEN
echo.
pause
goto :MAIN

:: ============================================================
::  Batch folder - choose format + mode
:: ============================================================
:FMT_BATCH
cls
echo.
echo  +------------------------------------------+
echo  ^|  Crimson Desert PAM/PAMLOD Extractor     ^|
echo  ^|  v3.2                                    ^|
echo  +------------------------------------------+
echo.
echo    Folder: !BATCH_DIR!
echo.
echo    Format:
echo      1 - FBX  (Binary FBX 7.4)
echo      2 - OBJ  (Wavefront OBJ + MTL)
echo.
choice /c 12 /n /m "  Choose [1-2]: "
if errorlevel 2 ( set "FORMAT=obj" ) else ( set "FORMAT=fbx" )

cls
echo.
echo  +------------------------------------------+
echo  ^|  Crimson Desert PAM/PAMLOD Extractor     ^|
echo  ^|  v3.2                                    ^|
echo  +------------------------------------------+
echo.
echo    Folder: !BATCH_DIR!
echo    Format: !FORMAT!
echo.
echo    Mode:
echo      1 - Combined   (all submeshes in one file)
echo      2 - Split      (one file per submesh)
echo.
choice /c 12 /n /m "  Choose [1-2]: "
if errorlevel 2 ( set "EMODE=--split" ) else ( set "EMODE=" )

set "COUNT=0"
for %%F in ("!BATCH_DIR!\*.pam") do set /a COUNT+=1

if !COUNT!==0 (
    echo.
    echo  No PAM files found in: !BATCH_DIR!
    echo.
    pause & goto :MAIN
)

cls
echo.
echo  +------------------------------------------+
echo  ^|  Crimson Desert PAM/PAMLOD Extractor     ^|
echo  ^|  v3.2                                    ^|
echo  +------------------------------------------+
echo.
echo  Folder: !BATCH_DIR!
echo  Format: !FORMAT!   Mode: !EMODE!
echo  ------------------------------------------
echo.

set "DONE=0"
set "FAILED=0"

for %%F in ("!BATCH_DIR!\*.pam") do (
    set /a DONE+=1
    set "BSTEM=%%~nF"
    set "BSRC=%%~dpF"
    set "BOUT=!BATCH_DIR!\%%~nF"

    echo  [!DONE!/!COUNT!] %%~nxF

    python "!EXTRACTOR!" "%%F" --format !FORMAT! -o "!BOUT!" !EMODE!
    if errorlevel 1 (
        set /a FAILED+=1
        echo         PAM: FAILED
    ) else (
        echo         PAM: OK
    )

    if exist "!BSRC!!BSTEM!.pamlod" (
        python "!EXTRACTOR!" "!BSRC!!BSTEM!.pamlod" --format !FORMAT! -o "!BOUT!"
        if errorlevel 1 (
            echo         LOD: FAILED
        ) else (
            echo         LOD: OK
        )
    ) else (
        echo         LOD: not found
    )

    set "BDDS=0"
    for /f "usebackq delims=" %%D in (`powershell -noprofile -executionpolicy bypass -file "%SCRIPT_DIR%pick.ps1" dds "!BSTEM!" "!BSRC:~0,-1!"`) do (
        if not exist "!BOUT!" mkdir "!BOUT!"
        copy /y "%%D" "!BOUT!\" >nul 2>&1
        set /a BDDS+=1
    )
    if !BDDS!==0 (
        echo         DDS: none
    ) else (
        echo         DDS: !BDDS! copied
    )
    echo.
)

set /a BATCH_OK=DONE-FAILED
echo  ------------------------------------------
echo  Batch done: !BATCH_OK! / !DONE! succeeded
if !FAILED! gtr 0 echo  Failed: !FAILED!
echo  ------------------------------------------
echo.
choice /c YN /n /m "  Open folder? [Y/N]: "
if errorlevel 2 goto :SKIP_BATCH_OPEN
start "" explorer "!BATCH_DIR!"
:SKIP_BATCH_OPEN
echo.
pause
goto :MAIN
