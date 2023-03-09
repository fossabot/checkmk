@echo off
:: Script to Build Rust executable and sign it
:: 
:: Sign mode:
:: cargo_build file password
:: file is located in c:\common\store and must be well protected from access
:: The password is delivered by jenkins(in a turn from our password store)
:: In future we could download file too(from the password store), but this will 
:: not change the requirement to protect the file from free access.
::
:: Standard Mode:
:: cargo_build
::

SETLOCAL EnableDelayedExpansion

echo --- info ---
echo PATH=%path%
echo ------------
set RUST_BACKTRACE=1

:: Jenkins calls windows scripts in a quite strange manner, better to check is cargo available
where cargo > nul
if not %errorlevel% == 0 powershell Write-Host "Cargo not found, please install it and/or add to PATH" -Foreground Red && exit /b 7

set cur_dir=%cd%
set arte=%cur_dir%\..\..\artefacts
:: 64-bit
::set target=x86_64-pc-windows-mscvc
:: 32-bit
set target=i686-pc-windows-msvc
set exe_name=cmk-agent-ctl.exe
set exe=target\%target%\release\%exe_name%
rustup toolchain list
rustup default 1.66.0
rustup target add %target%
rustup update 1.66.0
@echo RUST versions:
cd
cargo -V
rustc -V

:: On windows we want to kill exe before starting rebuild. 
:: Use case CI starts testing, for some reasoms process hangs up longer as expected thus 
:: rebuild/retest will be not possible: we get strange/inconsistent results.
call scripts\kill_processes_in_targets.cmd %target%\release

:: Disable assert()s in C/C++ parts (e.g. wepoll-ffi), they map to _assert()/_wassert(),
:: which is not provided by libucrt. The latter is needed for static linking.
:: https://github.com/rust-lang/cc-rs#external-configuration-via-environment-variables
set CFLAGS=-DNDEBUG

del /Q %arte%\%exe_name% 2> nul

:: 1. Clippy
powershell Write-Host "Run Rust clippy" -Foreground White
cargo clippy --release --target %target% 2>&1
if not "%errorlevel%" == "0" powershell Write-Host "Failed cargo clippy" -Foreground Red && exit /b 17
powershell Write-Host "Checking Rust SUCCESS" -Foreground Green

:: 2. Build
powershell Write-Host "Building Rust executables" -Foreground White
cargo build --release --target %target% 2>&1
if not "%errorlevel%" == "0" powershell Write-Host "Failed cargo build" -Foreground Red && exit /b 18
powershell Write-Host "Building Rust SUCCESS" -Foreground Green

:: 3. Test
:: Validate elevation, because full testing is possible only in elevated mode!
net session > nul 2>&1 
IF NOT %ERRORLEVEL% EQU 0 (
    echo You must be elevated. Exiting...
    exit /B 21
)
powershell Write-Host "Testing Rust executables" -Foreground White
cargo test --release --target %target% -- --test-threads=4 2>&1
if not "%errorlevel%" == "0" powershell Write-Host "Failed cargo build" -Foreground Red && exit /b 19
powershell Write-Host "Testing Rust SUCCESS" -Foreground Green

:: 4. [optional] Signing
if not "%2" == "" (
powershell Write-Host "Signing Rust executables" -Foreground White
@call ..\wnx\sign_windows_exe c:\common\store\%1 %2 %exe%
if not %errorlevel% == 0 powershell Write-Host "Failed signing %exe%" -Foreground Red && exit /b 20
)

:: 5. Storing artifacts
powershell Write-Host "Uploading artifacts: [ %exe% ] ..." -Foreground White
copy %exe% %arte%\%exe_name%
powershell Write-Host "Done." -Foreground Green

