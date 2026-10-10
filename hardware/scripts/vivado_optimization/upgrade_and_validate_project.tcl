set script_dir [file dirname [file normalize [info script]]]
set workspace [file normalize [file join $script_dir ..]]
set vivado_root [file join $workspace pl pl vivado]
set project_dir [file join $vivado_root project_1intrusion_detection_optimized]
set project_file [file join $project_dir project_1intrusion_detection_optimized.xpr]
set optimized_repo [file join $vivado_root ip_repo_optimized]
set report_dir [file join $script_dir reports]

file mkdir $report_dir
open_project $project_file
set_property ip_repo_paths [list $optimized_repo] [current_project]
update_ip_catalog -rebuild

set bd_file [get_files -quiet */design_1.bd]
open_bd_design $bd_file
report_ip_status -file [file join $report_dir ip_status_before_upgrade.rpt]

set hls_ips [get_ips -quiet {
    design_1_rgb2gray_0_0
    design_1_frame_diff_0_0
    design_1_threshold_0_0
    design_1_morphology_0_0
}]
if {[llength $hls_ips] != 4} {
    error "Expected four HLS IP instances, found: $hls_ips"
}

upgrade_ip $hls_ips
validate_bd_design
save_bd_design
generate_target all $bd_file
export_ip_user_files -of_objects $bd_file -no_script -sync -force -quiet
update_compile_order -fileset sources_1

report_ip_status -file [file join $report_dir ip_status_after_upgrade.rpt]
write_bd_tcl -force [file join $report_dir optimized_design_1.tcl]

set address_spaces [get_bd_addr_spaces -of_objects [get_bd_cells processing_system7_0]]
foreach address_space $address_spaces {
    foreach segment [get_bd_addr_segs -of_objects $address_space] {
        puts "ADDRESS_SEGMENT=[get_property NAME $segment] OFFSET=[get_property OFFSET $segment] RANGE=[get_property RANGE $segment]"
    }
}

foreach cell_name {rgb2gray_0 frame_diff_0 threshold_0 morphology_0} {
    set cell [get_bd_cells $cell_name]
    puts "BD_CELL=$cell_name VLNV=[get_property VLNV $cell]"
}

close_project
exit
