set script_dir [file dirname [file normalize [info script]]]
set workspace [file normalize [file join $script_dir ..]]
set project_file [file join $workspace pl pl vivado project_1intrusion_detection_optimized project_1intrusion_detection_optimized.xpr]
set report_dir [file join $script_dir reports optimized_post_route_phys_opt]

file mkdir $report_dir
open_project $project_file
set_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.IS_ENABLED true [get_runs impl_1]
launch_runs impl_1 -to_step {phys_opt_design (Post-Route)} -jobs 4
wait_on_run impl_1

set impl_status [get_property STATUS [get_runs impl_1]]
puts "IMPL_STATUS=$impl_status"
open_run impl_1

report_timing_summary -delay_type min_max -report_unconstrained -max_paths 10 -file [file join $report_dir timing_summary.rpt]
report_utilization -hierarchical -file [file join $report_dir utilization.rpt]
report_drc -file [file join $report_dir drc.rpt]
report_route_status -file [file join $report_dir route_status.rpt]

set setup_path [get_timing_paths -quiet -delay_type max -max_paths 1]
set hold_path [get_timing_paths -quiet -delay_type min -max_paths 1]
if {[llength $setup_path] > 0} {
    puts "POST_PHYS_OPT_WNS=[get_property SLACK $setup_path]"
}
if {[llength $hold_path] > 0} {
    puts "POST_PHYS_OPT_WHS=[get_property SLACK $hold_path]"
}
puts "BITSTREAM_COMMAND_EXECUTED=NO"

close_design
close_project
exit
