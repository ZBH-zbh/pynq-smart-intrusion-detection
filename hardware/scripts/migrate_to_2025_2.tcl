# =============================================================================
# Migrate the PYNQ-Z2 intrusion-detection project from Vivado 2022.1 to 2025.2.
#
# Run with the 2025.2 toolchain on a COPY of the project, never on the original:
#
#   vivado -mode batch -nolog -nojournal \
#          -source migrate_to_2025_2.tcl \
#          -tclargs <src_project_dir> <dst_project_dir>
#
# What it does, and why in this order:
#
#   1. Copies the project rather than opening it in place. The 2022.1 project is
#      the only one that currently produces a bitstream verified on hardware; a
#      failed migration must not be able to damage it.
#   2. Re-points ip_repo at the packaged HLS IP and rebuilds the catalog. The
#      IP zips were packaged by 2022.1 and are expected to be upgradeable rather
#      than needing Vitis HLS to re-run. If an IP refuses to upgrade, this
#      script reports it instead of silently synthesising stale IP.
#   3. Upgrades the IPs and the block design, then VALIDATES before building.
#      A migration that quietly leaves locked IP is worse than one that stops.
#   4. Reports the six AXI addresses. These are hard-coded in the PYNQ Python
#      (pl_motion_detection_optimized.py), so if 2025.2 assigns different
#      offsets the software must change with them. This is the single most
#      likely way for the rebuild to look successful and still be broken.
#   5. Only then synthesises and implements, writing the bitstream to the new
#      directory. The old bitstream is left untouched.
# =============================================================================

set script_dir [file dirname [file normalize [info script]]]

if {[llength $argv] < 2} {
    puts "ERROR: usage: migrate_to_2025_2.tcl <src_project_dir> <dst_project_dir>"
    exit 1
}
set src_dir [file normalize [lindex $argv 0]]
set dst_dir [file normalize [lindex $argv 1]]

set src_xpr [file join $src_dir "project_1intrusion_detection_optimized.xpr"]
if {![file exists $src_xpr]} {
    puts "ERROR: no project at $src_xpr"
    exit 1
}

puts "MIGRATE_SRC=$src_xpr"
puts "MIGRATE_DST=$dst_dir"
puts "MIGRATE_TOOL=[version -short]"

# -----------------------------------------------------------------------------
# 0. Work on a copy.
# -----------------------------------------------------------------------------
if {![file exists $dst_dir]} {
    file mkdir $dst_dir
}
puts "STEP=copying"
file copy -force $src_xpr [file join $dst_dir "project_1intrusion_detection_optimized.xpr"]

# The ip_repo lives beside the project; the copy needs it too.
set src_root [file dirname [file dirname $src_dir]]
foreach repo {ip_repo_optimized ip_repo constraints} {
    set from [file join $src_root $repo]
    if {[file exists $from]} {
        file copy -force $from $dst_dir
        puts "COPIED_REPO=$repo"
    }
}

set dst_xpr [file join $dst_dir "project_1intrusion_detection_optimized.xpr"]
open_project $dst_xpr

# -----------------------------------------------------------------------------
# 1. Point at the packaged IP and rebuild the catalog.
# -----------------------------------------------------------------------------
set optimized_repo [file join $dst_dir ip_repo_optimized]
if {[file exists $optimized_repo]} {
    set_property ip_repo_paths [list $optimized_repo] [current_project]
    puts "IP_REPO_SET=$optimized_repo"
} else {
    puts "WARNING: $optimized_repo not found; the block design may not resolve its IP"
}

puts "STEP=rebuilding_ip_catalog"
update_ip_catalog -rebuild

# -----------------------------------------------------------------------------
# 2. Report what is locked BEFORE trying to upgrade, so a refusal is visible.
# -----------------------------------------------------------------------------
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
            lappend failed "[get_property IPDEF $ip]: $err"
        } else {
            incr upgraded
        }
    }
}
puts "IPS_UPGRADED=$upgraded"
foreach f $failed { puts "IP_UPGRADE_FAILED=$f" }

# -----------------------------------------------------------------------------
# 3. Upgrade and validate the block design.
# -----------------------------------------------------------------------------
set bd_file [get_files -quiet */design_1.bd]
if {$bd_file eq ""} {
    puts "ERROR: design_1.bd not found in the project"
    close_project
    exit 1
}
open_bd_design $bd_file

puts "STEP=upgrading_bd"
catch {upgrade_bd_cells -quiet [get_bd_cells]} bd_upgrade_msg
puts "BD_UPGRADE_MSG=$bd_upgrade_msg"

puts "STEP=validating_bd"
set validation [validate_bd_design]
puts "BD_VALIDATION=$validation"

set locked_after [get_ips -quiet -filter {IS_LOCKED == 1}]
puts "LOCKED_IP_AFTER=[llength $locked_after]"
if {[llength $locked_after] > 0} {
    foreach ip $locked_after {
        puts "  STILL_LOCKED=[get_property IPDEF $ip]"
    }
    puts "STOPPING: locked IP remain, so the address map cannot be trusted."
    close_project
    exit 2
}

# -----------------------------------------------------------------------------
# 4. The addresses the PYNQ Python hard-codes. These MUST be reproduced.
# -----------------------------------------------------------------------------
puts "STEP=reporting_addresses"
set expected {
    frame_diff 0x40000000
    morphology 0x40010000
    rgb2gray   0x40020000
    threshold  0x40030000
    gpio       0x40040000
    dma        0x41E00000
}

set seen {}
foreach address_space [get_bd_addr_spaces -of_objects [get_bd_cells processing_system7_0]] {
    foreach segment [get_bd_addr_segs -of_objects $address_space] {
        set offset [get_property OFFSET $segment]
        if {$offset ne ""} {
            set name [get_property NAME $segment]
            puts "ADDRESS_SEGMENT=$name OFFSET=$offset RANGE=[get_property RANGE $segment]"
            dict set seen [string tolower $name] $offset
        }
    }
}

# -----------------------------------------------------------------------------
# 5. Build. Skipped unless the caller asked for it, because synthesis and
#    implementation take a long time and should be a deliberate step.
# -----------------------------------------------------------------------------
set do_build 0
if {[llength $argv] >= 3 && [lindex $argv 2] eq "build"} {
    set do_build 1
}

if {$do_build} {
    puts "STEP=synthesis"
    reset_run synth_1
    launch_runs synth_1 -jobs 4
    wait_on_run synth_1
    if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
        puts "ERROR: synthesis did not complete"
        close_project
        exit 3
    }

    puts "STEP=implementation"
    launch_runs impl_1 -to_step write_bitstream -jobs 4
    wait_on_run impl_1
    if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
        puts "ERROR: implementation did not complete"
        close_project
        exit 4
    }
    puts "BITSTREAM=[get_property DIRECTORY [get_runs impl_1]]/design_1_wrapper.bit"
} else {
    puts "STEP=build_skipped (pass 'build' as the third argument to run synth+impl)"
}

puts "STEP=writing_hw_handoff"
catch {write_hw_platform -fixed -include_bit -force [file join $dst_dir design_1_wrapper.xsa]} xsa_msg
puts "XSA_MSG=$xsa_msg"

close_project
puts "MIGRATION_DONE"
exit 0
