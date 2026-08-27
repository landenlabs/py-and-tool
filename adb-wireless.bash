#!/usr/bin/env bash

# Wireless ADB Switcher for macOS

PORT=5555

case "$1" in
  on)
    # If IP is provided as argument 2, connect directly
    if [ -n "$2" ]; then
      TARGET_IP="$2"
    else
      echo "Scanning for USB-connected Android device..."
      # Attempt to auto-detect device Wi-Fi IP via ADB shell
      TARGET_IP=$(adb shell ip route 2>/dev/null | awk '/wlan0/ {print $9}' | head -n 1)
      
      if [ -z "$TARGET_IP" ]; then
        echo "Error: No USB device detected and no IP provided."
        echo "Usage: ./wifi-adb.sh on [IP_ADDRESS]"
        exit 1
      fi
      
      echo "Device IP detected: $TARGET_IP"
      echo "Restarting ADB in TCP mode..."
      adb tcpip $PORT
      sleep 2
    fi

    echo "Connecting to $TARGET_IP:$PORT..."
    adb connect "$TARGET_IP:$PORT"
    echo "You can now unplug the USB cable."
    ;;

  off)
    echo "Disconnecting wireless ADB..."
    adb disconnect
    echo "Resetting ADB to USB mode..."
    adb usb
    ;;

  *)
    echo "Usage: $0 {on [IP]|off}"
    echo ""
    echo "Examples:"
    echo "  $0 on               Auto-detects IP from USB device, enables TCP, and connects."
    echo "  $0 on 192.168.1.50  Connects directly to the specified local IP."
    echo "  $0 off              Disconnects wireless ADB and resets port to USB."
    exit 1
    ;;
esac
