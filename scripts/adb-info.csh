#!/bin/csh -f

#
# Get Package information  
#
#  Example:
#   ./adb-info.csh 'packagePattern'  
#

set adb="adb"  # default any connected device
set devices=`adb devices | egrep '\t(device|unauthorized)' | sed -E 's/\tdevice//'`
set numDevices=`echo $devices | wc -w`
if ($numDevices != 1) then
  echo --- Multiple devices ---
  set devIdx = 1
  foreach dev ($devices) 
     echo "  $devIdx  $dev"
     @ devIdx++
  end
  echo -n "Enter device # (1..$numDevices):"
  set devIdx = $<
  set adb="adb -s $devices[$devIdx]"
  echo "  Adb set to: $adb"
  echo
else
  adb devices
  set adb="adb -s $devices[1]"
endif

$adb shell getprop ro.product.model

if ($#argv != 1)  then
  echo "Get packages matching pattern quoted"
  echo "   ./adb-info.csh 'achme'"
  echo "   ./adb-info.csh 'achme.*hm'"
  gecho -e "\nRemember to escape patterns"
  gecho -e "\n===  TWC apps installed ==="
  # ($adb shell pm list package && $adb shell pm list package -a) | sort -u | grep -i achme
  set pkgs=`($adb shell pm list package && $adb shell pm list package -a) | sed -E 's/package://' | sort -u | grep -i achme`
  foreach pkg ($pkgs) 
    set lastUpdate=`$adb shell dumpsys package $pkg | grep last`
    echo "$lastUpdate  $pkg"
  end
  
  # gecho -e "\n===  Active activities  ==="
  # $adb shell dumpsys activity activities | grep -B20 mActivityComponent | egrep '(mActivityComponent|Intent |\* Task)'
  # gecho -e "\n===  Package activities  ==="
  # $adb shell dumpsys package | grep Activity | grep -i "$pkgPattern"
  exit
endif

set pkgPattern="$1"
foreach pkg ( `($adb shell pm list package && $adb shell pm list package -a) | sort -u | grep "$pkgPattern"` )
    set dst=`echo $pkg | sed -E 's/.*://'`
    echo  "---- $dst ---"
    $adb shell dumpsys package $dst | egrep '(installerPackage|signatures| Package )' | sed -E 's/^ */  /'
    echo "--Exported Activities: $adb shell dumpsys package $dst | grep Activity"
    $adb shell dumpsys package $dst | grep --color=never Activity  | sed -E 's/^ */  /'
    echo "--Versions: $adb shell dumpsys package $dst| grep version[NC]"
    $adb shell dumpsys package $dst| egrep --color=never "(version[NC]|Hidden )"  | sed -E 's/^ */  /'
    echo "--Last Install: $adb shell dumpsys package $dst| grep last"
    $adb shell dumpsys package $dst| egrep --color=never "(last|Hidden )"  | sed -E 's/^ */  /'
    echo "--Flags: $adb shell dumpsys package $dst| grep -i flags"
    $adb shell dumpsys package $dst| grep -i flags
    echo "--User Permissions--"
    $adb shell dumpsys package $dst | awk '/User [0-9]+\:/{u=$2; print; if (index($0, "installed=true")==0)u="-";p=0;}/runtime permissions/{p=1}/ *android.permission/{ if (p==1 && u!="-") printf "    user %2d, %15s %s\n",u,$2,$1;}'  | egrep '(user|true)'
    echo "--User Permissions denied once (USER_SET), denied twice (USER_FIXED)--"
    $adb shell dumpsys package $dst | egrep '(USER_SET|USER_FIXED)'
    
   
    set pkgPath=`$adb shell pm path $dst | sed -E 's/package://'`
    if ("$pkgPath" != "") then
        echo "--File package info: $adb shell stat $pkgPath"
        $adb shell stat "$pkgPath"
    endif
    
    echo
end

gecho -e "\n===  Active activities  ==="
adb shell dumpsys activity activities | grep -B20 mActivityComponent | egrep '(mActivityComponent|Intent |\* Task)' | grep "$pkgPattern"


gecho -e "\n\n"
