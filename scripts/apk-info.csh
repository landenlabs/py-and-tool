#!/bin/csh -f


if ("$#argv" == "0" || $#argv < 1) then
  echo "Extract version and more from APK file."
  echo "  ./apk-info.csh build/outputs/apk/*.apk"
  exit
endif

# echo "#file=$#argv"
# set file1=$1
# echo $*
set path=( $path ~/Library/Android/sdk/build-tools/37.0.0 )

foreach apk ($*)
   echo "$apk "
   echo "=== Versions ( aapt dump badging $apk | grep -i package) ==="
   aapt dump badging $apk | grep -i package | tr ' ' '\n' | sed -E 's/^/    /'

   echo "=== Versions ( aapt dump badging $apk | grep -i version) ==="
   aapt dump badging $apk | grep -i version | tr ' ' '\n' | egrep  name | sed -E 's/^ */    /'
   aapt dump badging $apk | grep -i version | tr ' ' '\n' | egrep -i '(version)' | sed -E 's/^ */    /' | sort

   echo
   echo "=== permission (aapt dump badging $apk | grep -i permission )  ==="
   aapt dump badging $apk | grep -i permission | sed -E 's/.*name=/   /' | sort

   echo
   echo "=== other ==="
   aapt dump badging $apk | egrep -i '(debug|native|testOnly|launchable)' | sed -E 's/^ */    /'

  end


