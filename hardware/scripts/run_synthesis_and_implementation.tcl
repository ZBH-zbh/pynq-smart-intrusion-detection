set script_dir [file dirname [file normalize [info script]]]
set workspace [file normalize [file join $script_dir ..]]
set vivado_root [file join $workspace pl pl vivado]
set project_file [file join $vivado_root project_1intrusion_detection_optimized project_1intrusion_detection_optimized.xpr]
set optimized_repo [file join $vivado_root ip_repo_optimized]
set report_dir [file join $script_dir reports optimized_implementation]

file mkdir $report_dir
open_project $project_file
set_property ip_repo_paths [list $optimized_repo] [current_project]
update_ip_catalog -rebuild

set bd_file [get_files -quiet */design_1.bd]
open_bd_design $bd_file
validate_bd_design
generate_target all $bd_file
update_compile_order -fileset sources_1

reset_run impl_1
reset_run synth_1
set_property AUTO_INCREMENTAL_CHECKPOINT 0 [get_runs synth_1]
set_property INCREMENTAL_CHECKPOINT {} [get_runs synth_1]

launch_runs synth_1 -jobs 4
wait_on_run synth_1
set synth_status [get_property STATUS [get_runs synth_1]]
puts "SYNTH_STATUS=$synth_status"
if {![string match "*Complete*" $synth_status]} {
    error "Synthesis did not complete successfully: $synth_status"
}

open_run synth_1
report_utilization -hierarchical -file [file join $report_dir post_synth_utilization.rpt]
close_design

launch_runs impl_1 -to_step route_design -jobs 4
wait_on_run impl_1
set impl_status [get_property STATUS [get_runs impl_1]]
puts "IMPL_STATUS=$impl_status"
if {![string match "*Complete*" $impl_status]} {
    error "Implementation did not reach route_design successfully: $impl_status"
}

open_run impl_1
report_timing_summary -delay_type min_max -report_unconstrained -max_paths 10 -file [file join $report_dir post_route_timing_summary.rpt]
report_utilization -hierarchical -file [file join $report_dir post_route_utilization.rpt]
report_drc -file [file join $report_dir post_route_drc.rpt]
report_route_status -file [file join $report_dir post_route_status.rpt]
report_methodology -file [file join $report_dir post_route_methodology.rpt]

set setup_path [get_timing_paths -quiet -delay_type max -max_paths 1]
set hold_path [get_timing_paths -quiet -delay_type min -max_paths 1]
if {[llength $setup_path] > 0} {
    puts "POST_ROUTE_WNS=[get_property SLACK $setup_path]"
}
if {[llength $hold_path] > 0} {
    puts "POST_ROUTE_WHS=[get_property SLACK $hold_path]"
}

puts "BITSTREAM_COMMAND_EXECUTED=NO"
close_design
close_project
exit
