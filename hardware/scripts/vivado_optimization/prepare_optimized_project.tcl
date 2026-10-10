set script_dir [file dirname [file normalize [info script]]]
set workspace [file normalize [file join $script_dir ..]]
set vivado_root [file join $workspace pl pl vivado]
set source_project [file join $vivado_root project_1intrusion_detection project_1intrusion_detection.xpr]
set optimized_name project_1intrusion_detection_optimized
set optimized_dir [file join $vivado_root $optimized_name]
set optimized_repo [file join $vivado_root ip_repo_optimized]
set report_dir [file join $script_dir reports]

if {[file exists $optimized_dir]} {
    error "Optimized project directory already exists: $optimized_dir"
}

file mkdir $report_dir
open_project $source_project
save_project_as $optimized_name $optimized_dir

set_property ip_repo_paths [list $optimized_repo] [current_project]
update_ip_catalog -rebuild

set bd_file [get_files -quiet */design_1.bd]
if {[llength $bd_file] != 1} {
    error "Expected exactly one design_1.bd, found: $bd_file"
}

open_bd_design $bd_file
validate_bd_design
save_bd_design

report_ip_status -file [file join $report_dir ip_status_after_repo_switch.rpt]
report_property [current_project] -file [file join $report_dir project_properties.rpt]

puts "OPTIMIZED_PROJECT=[get_property DIRECTORY [current_project]]"
puts "IP_REPO_PATHS=[get_property ip_repo_paths [current_project]]"
foreach cell_name {rgb2gray_0 frame_diff_0 threshold_0 morphology_0} {
    set cell [get_bd_cells $cell_name]
    puts "BD_CELL=$cell_name VLNV=[get_property VLNV $cell]"
}

close_project
exit
