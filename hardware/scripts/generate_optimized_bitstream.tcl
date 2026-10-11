set script_dir [file dirname [file normalize [info script]]]
set workspace [file normalize [file join $script_dir ..]]
set run_dir [file join $workspace pl pl vivado project_1intrusion_detection_optimized project_1intrusion_detection_optimized.runs impl_1]
set routed_dcp [file join $run_dir design_1_wrapper_postroute_physopt.dcp]
set output_dir [file join $workspace optimized_overlay]
set bit_file [file join $output_dir intrusion_detection_optimized.bit]

if {![file exists $routed_dcp]} {
    error "Post-route physical-optimization checkpoint not found: $routed_dcp"
}
if {[file exists $bit_file]} {
    error "Refusing to overwrite existing optimized bitstream: $bit_file"
}

file mkdir $output_dir
open_checkpoint $routed_dcp
report_timing_summary -delay_type min_max -report_unconstrained -max_paths 10 -file [file join $output_dir timing_summary_before_bitstream.rpt]
report_drc -file [file join $output_dir drc_before_bitstream.rpt]
write_bitstream $bit_file

if {![file exists $bit_file]} {
    error "Expected bitstream was not created: $bit_file"
}
puts "BITSTREAM_FILE=$bit_file"
puts "BITSTREAM_SIZE=[file size $bit_file]"

close_design
exit
