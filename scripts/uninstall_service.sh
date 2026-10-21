#!/bin/bash
# Stop the background JobPrep service and remove it from login items.
LABEL="com.jobprep.server"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "JobPrep background service removed."
