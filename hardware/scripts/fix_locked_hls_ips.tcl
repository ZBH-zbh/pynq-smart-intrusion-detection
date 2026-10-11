# Diagnose and clear the locked state after replacing the HLS IP in the repo.
#
# The IP zips were regenerated with 2025.2 under the SAME VLNV
# (xilinx.com:hls:frame_diff:1.0). Vivado therefore does not treat them as a
# version upgrade -- upgrade_ip reports success yet the block-design instances
# stay locked, because their cached output products no longer match the IP
# definition. report_ip_status is the documented way to find out what it wants.

set project_dir [file normalize [lindex $argv 0]]
set xprs [glob -nocomplain -directory $project_dir *.xpr]
set xpr [lindex $xprs 0]

puts "TOOL=[version -short]"
open_project $xpr

set repo [file join [file dirname $project_dir] ip_repo_optimized]
set_property ip_repo_paths [list $repo] [current_project]
update_ip_catalog -rebuild

puts "STEP=report_ip_status_before"
report_ip_status -file [file join $project_dir ip_status_before.rpt]

set bd_file [get_files -quiet */design_1.bd]
open_bd_design $bd_file

puts "STEP=resetting_ip_output_products"
foreach ip [get_ips -quiet] {
    if {[get_property IS_LOCKED $ip]} {
        puts "  resetting [get_property IPDEF $ip]"
        catch {reset_target all $ip} msg
        if {$msg ne ""} { puts "    reset: $msg" }
    }
}

puts "STEP=upgrade_ip_force"
foreach ip [get_ips -quiet] {
    if {[get_property IS_LOCKED $ip]} {
        if {[catch {upgrade_ip -force -quiet $ip} err]} {
            puts "  upgrade_ip -force failed for [get_property IPDEF $ip]: $err"
        } else {
            puts "  upgraded [get_property IPDEF $ip]"
        }
    }
}

set still [get_ips -quiet -filter {IS_LOCKED == 1}]
puts "LOCKED_AFTER_UPGRADE=[llength $still]"

if {[llength $still] > 0} {
    puts "STEP=generate_target_all"
    foreach ip $still {
        catch {generate_target all $ip} msg
        puts "  generate_target [get_property IPDEF $ip]: $msg"
    }
    set still2 [get_ips -quiet -filter {IS_LOCKED == 1}]
    puts "LOCKED_AFTER_GENERATE=[llength $still2]"
    if {[llength $still2] > 0} {
        puts "STEP=report_ip_status_after"
        report_ip_status -file [file join $project_dir ip_status_after.rpt]
    }
}

puts "STEP=validate"
if {[catch {validate_bd_design} v]} {
    puts "VALIDATE_FAILED=$v"
} else {
    puts "VALIDATE_OK=$v"
}

puts "STEP=addresses"
foreach address_space [get_bd_addr_spaces -of_objects [get_bd_cells processing_system7_0]] {
    foreach segment [get_bd_addr_segs -of_objects $address_space] {
        set offset [get_property OFFSET $segment]
        if {$offset ne ""} {
            puts "ADDRESS_SEGMENT=[get_property NAME $segment] OFFSET=$offset"
        }
    }
}

catch {save_bd_design} sm
puts "SAVE=$sm"
close_project
puts "DIAG_DONE"
exit 0
