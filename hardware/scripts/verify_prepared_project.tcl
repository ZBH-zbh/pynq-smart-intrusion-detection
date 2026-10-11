set script_dir [file dirname [file normalize [info script]]]
set workspace [file normalize [file join $script_dir ..]]
set vivado_root [file join $workspace pl pl vivado]
set project_file [file join $vivado_root project_1intrusion_detection_optimized project_1intrusion_detection_optimized.xpr]
set optimized_repo [file join $vivado_root ip_repo_optimized]
set report_dir [file join $script_dir reports]

open_project $project_file
set_property ip_repo_paths [list $optimized_repo] [current_project]
update_ip_catalog -rebuild

set bd_file [get_files -quiet */design_1.bd]
open_bd_design $bd_file
validate_bd_design
report_ip_status -file [file join $report_dir ip_status_verified.rpt]

set locked_ips [get_ips -quiet -filter {IS_LOCKED == 1}]
puts "LOCKED_IP_COUNT=[llength $locked_ips]"
puts "PROJECT_IP_REPO=[get_property ip_repo_paths [current_project]]"

set address_spaces [get_bd_addr_spaces -of_objects [get_bd_cells processing_system7_0]]
foreach address_space $address_spaces {
    foreach segment [get_bd_addr_segs -of_objects $address_space] {
        set offset [get_property OFFSET $segment]
        if {$offset ne ""} {
            puts "ADDRESS_SEGMENT=[get_property NAME $segment] OFFSET=$offset RANGE=[get_property RANGE $segment]"
        }
    }
}

close_project
exit
