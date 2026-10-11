# What revision does the catalog report for the rebuilt HLS IP?
#
# The block design pins "ip_revision": "2114784485" per instance, recorded at the
# time the 2022.1 IP was added. Replacing the IP in the repo under the same VLNV
# changes its revision, so the BD can no longer resolve it and reports "IP
# definition not found". This prints what the catalog actually offers, which is
# the number the BD has to be aligned to.

set project_dir [file normalize [lindex $argv 0]]
set xprs [glob -nocomplain -directory $project_dir *.xpr]
open_project [lindex $xprs 0]

set repo [file join [file dirname $project_dir] ip_repo_optimized]
puts "REPO=$repo"

set_property ip_repo_paths [list $repo] [current_project]
update_ip_catalog -rebuild

foreach name {frame_diff morphology rgb2gray threshold} {
    set defs [get_ipdefs -all -quiet xilinx.com:hls:${name}:1.0]
    if {[llength $defs] == 0} {
        puts "IPDEF_MISSING=$name"
        continue
    }
    foreach d $defs {
        puts "IPDEF=$name vlnv=[get_property VLNV $d] revision=[get_property REVISION $d]"
    }
}

puts "STEP=bd_revisions"
set bd_file [get_files -quiet */design_1.bd]
if {$bd_file ne ""} {
    set handle [open $bd_file r]
    set data [read $handle]
    close $handle
    foreach line [split $data \n] {
        if {[string match *ip_revision* $line]} {
            puts "BD_LINE=[string trim $line]"
        }
    }
}
close_project
puts "REV_DIAG_DONE"
exit 0
