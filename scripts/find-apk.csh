#!/bin/tcsh -f 

set noColor='\033[0m'  
set black='\033[0;30m'        # Black
set red='\033[0;31m'          # Red
set green='\033[0;32m'        # Green
set yellow='\033[0;33m'       # Yellow
set blue='\033[0;34m'         # Blue
set purple='\033[0;35m'       # Purple
set cyan='\033[0;36m'         # Cyan
set white='\033[0;37m'        # White


printf "${yellow}===== AAR ====${noColor}\n"
find . -type f -name \*.aar | xargs -IX stat -L -f "%N %t%z %t%Sm %t%N" X | sed -E 's,\./[^ ]*/,,' | sort | column -t  

printf "${yellow}===== APKs ====${noColor}\n"
find . -type f -name \*.apk | xargs -IX stat -L -f "%N %t%z %t%Sm %t%N" X | sed -E 's,\./[^ ]*/,,' | sort | column -t  

printf "${yellow}===== Bundles (aab) ====${noColor}\n"
find . -type f -name \*.aab | xargs -IX stat -L -f "%N %t%z %t%Sm %t%N" X | sed -E 's,\./[^ ]*/,,' | sort | column -t  

printf "${yellow}===== R8/Proguard ====${noColor}\n"
find . -name mapping.txt | xargs -IX wc -l X
