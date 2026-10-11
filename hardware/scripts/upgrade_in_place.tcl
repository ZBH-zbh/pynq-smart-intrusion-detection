# =============================================================================
# Upgrade the PYNQ-Z2 intrusion-detection project in place to the running
# Vivado version (2025.2), WITHOUT building.
#
#   vivado -mode batch -nolog -nojournal -source upgrade_in_place.tcl \
#          -tclargs <project_dir>
#
# Deliberately separate from building. Making the project open, resolve its IP
# and validate the block design is one decision; spending an hour on synthesis
# and implementation is another, and the second is only worth doing once the
# first is known good.
#
# The number this must reproduce is the AXI map, because the PYNQ Python
# hard-codes it:
#   frame_diff 0x40000000  morphology 0x40010000  rgb2gray 0x40020000
#   threshold  0x40030000  AXI GPIO   0x40040000  AXI DMA  0x41E00000
# If 2025.2 reassigns any of those, the software has to change with them, and
# that is exactly the kind of breakage a "successful build" would hide.
# =============================================================================

set script_dir [file dirname [file normalize [info script]]]

if {[llength $argv] < 1} {
    puts "ERROR: usage: upgrade_in_place.tcl <project_dir>"
    exit 1
}
set project_dir [file normalize [lindex $argv 0]]

# Find the .xpr without assuming which variant this is.
set xprs [glob -nocomplain -directory $project_dir *.xpr]
if {[llength $xprs] != 1} {
    puts "ERROR: expected exactly one .xpr in $project_dir, found [llength $xprs]"
    exit 1
}
set xpr [lindex $xprs 0]

puts "UPGRADE_TOOL=[version -short]"
puts "UPGRADE_XPR=$xpr"

open_project $xpr
# 'project' has no 'version' property in 2025.2; asking for it aborts the run.
puts "OPENED_VERSION=[version -short]"

# The IP repo sits beside the project directory.
set vivado_root [file dirname $project_dir]
foreach candidate {ip_repo_optimized ip_repo} {
    set repo [file join $vivado_root $candidate]
    if {[file exists $repo]} {
        set_property ip_repo_paths [list $repo] [current_project]
        puts "IP_REPO_SET=$repo"
        break
    }
}

puts "STEP=rebuilding_ip_catalog"
update_ip_catalog -rebuild

set locked_before [get_ips -quiet -filter {IS_LOCKED == 1}]
puts "LOCKED_IP_BEFORE=[llength $locked_before]"
foreach ip $locked_before {
    puts "  LOCKED_BEFORE=[get_property IPDEF $ip]"
}

puts "STEP=upgrading_ips"
set upgraded 0
set failed {}
foreach ip [get_ips -quiet] {
    if {[get_property IS_LOCKED $ip]} {
        if {[catch {upgrade_ip -quiet $ip} err]} {
            lappend failed "[get_property IPDEF $ip] :: $err"
        } else {
            incr upgraded
        }
    }
}
puts "IPS_UPGRADED=$upgraded"
foreach f $failed { puts "IP_UPGRADE_FAILED=$f" }

set bd_file [get_files -quiet */design_1.bd]
if {$bd_file eq ""} {
    puts "ERROR: design_1.bd not found"
    close_project
    exit 1
}
open_bd_design $bd_file

puts "STEP=upgrading_bd"
if {[catch {upgrade_bd_cells -quiet [get_bd_cells]} msg]} {
    puts "BD_UPGRADE_MSG=$msg"
} else {
    puts "BD_UPGRADE_MSG=ok"
}

puts "STEP=validating_bd"
set validation [validate_bd_design]
puts "BD_VALIDATION=$validation"

set locked_after [get_ips -quiet -filter {IS_LOCKED == 1}]
puts "LOCKED_IP_AFTER=[llength $locked_after]"
foreach ip $locked_after {
    puts "  STILL_LOCKED=[get_property IPDEF $ip]"
}

puts "STEP=reporting_addresses"
foreach address_space [get_bd_addr_spaces -of_objects [get_bd_cells processing_system7_0]] {
    foreach segment [get_bd_addr_segs -of_objects $address_space] {
        set offset [get_property OFFSET $segment]
        if {$offset ne ""} {
            puts "ADDRESS_SEGMENT=[get_property NAME $segment] OFFSET=$offset RANGE=[get_property RANGE $segment]"
        }
    }
}

# Compare against what the Python expects, so a moved peripheral is loud.
set expected {
    SEG_frame_diff_0_Reg  0x40000000
    SEG_morphology_0_Reg  0x40010000
    SEG_rgb2gray_0_Reg    0x40020000
    SEG_threshold_0_Reg   0x40030000
    SEG_axi_gpio_0_Reg    0x40040000
    SEG_axi_dma_0_Reg     0x41E00000
}
set mismatches 0
foreach {name want} $expected {
    set seg [get_bd_addr_segs -quiet */$name]
    if {$seg eq ""} {
        puts "MISSING_SEGMENT=$name"
        incr mismatches
        continue
    }
    set got [get_property OFFSET $seg]
    if {[string tolower $got] ne [string tolower $want]} {
        puts "ADDRESS_MISMATCH=$name expected=$want actual=$got"
        incr mismatches
    }
}
puts "ADDRESS_MISMATCHES=$mismatches"

puts "STEP=saving"
catch {save_bd_design} save_msg
puts "SAVE_BD=$save_msg"
close_project
puts "UPGRADE_DONE"
exit 0
