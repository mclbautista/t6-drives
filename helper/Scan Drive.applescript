-- Scan Drive Dock applet: picks a mounted drive and runs t6-drive-scan + t6-drive-send.

on run
	set volumesDir to do shell script "printf '%s' \"${T6_HELPER_VOLUMES_DIR:-/Volumes}\""
	set rawList to do shell script "/bin/zsh -c 'for p in \"$1\"/*(N); do [[ -d \"$p\" && ! -L \"$p\" ]] && print -r -- \"${p:t}\"; done' -- " & quoted form of volumesDir

	if rawList is "" then
		display dialog "No external drives are mounted." buttons {"OK"} default button "OK" with title "Scan Drive"
		return
	end if

	set volumeNames to paragraphs of rawList
	if (count of volumeNames) is 1 then
		set chosenName to item 1 of volumeNames
	else
		set picked to choose from list volumeNames with title "Scan Drive" with prompt "Choose a drive to scan:" default items {item 1 of volumeNames}
		if picked is false then
			return
		end if
		set chosenName to item 1 of picked
	end if

	set volumePath to volumesDir & "/" & chosenName
	set resourcesDir to ""
	try
		set resourcesDir to (POSIX path of (path to me)) & "Contents/Resources"
	end try

	try
		do shell script "/bin/zsh -c 'set -eo pipefail; vol=\"$1\"; res=\"$2\"; scan=\"\"; send=\"\"; for d in \"${T6_HELPER_BIN_DIR:-}\" \"$res\" /usr/local/libexec/t6-drives-helper /usr/local/bin /opt/homebrew/bin; do [ -n \"$d\" ] && [ -x \"$d/t6-drive-scan\" ] && [ -z \"$scan\" ] && scan=\"$d/t6-drive-scan\"; [ -n \"$d\" ] && [ -x \"$d/t6-drive-send\" ] && [ -z \"$send\" ] && send=\"$d/t6-drive-send\"; done; [ -n \"$scan\" ] || scan=$(command -v t6-drive-scan); [ -n \"$send\" ] || send=$(command -v t6-drive-send); \"$scan\" \"$vol\" | \"$send\"' -- " & quoted form of volumePath & " " & quoted form of resourcesDir
	on error errMsg
		display alert "Scan Drive" message errMsg as warning
	end try
end run
