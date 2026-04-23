set chromeApplication to "__CHROME_APPLICATION__"
set debugProbeUrl to "__DEBUG_PROBE_URL__"
set profileDir to "__PROFILE_DIR__"
set launchCommand to "open -na " & quoted form of chromeApplication & " --args --remote-debugging-port=__DEBUG_PORT__ --user-data-dir=" & quoted form of profileDir & " --no-first-run --no-default-browser-check"

try
    do shell script "/usr/bin/curl -fsS --max-time 1 " & quoted form of debugProbeUrl & " >/dev/null"
    do shell script "open -a " & quoted form of chromeApplication
on error
    try
        do shell script launchCommand
    on error errMsg
        display dialog "Failed to launch Bob Chrome: " & errMsg buttons {"OK"} default button "OK"
    end try
end try
