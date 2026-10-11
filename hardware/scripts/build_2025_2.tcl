# =============================================================================
# Build a 2025.2 bitstream for the PYNQ-Z2 project.
#
#   vivado -mode batch -nolog -nojournal -source build_2025_2.tcl \
#          -tclargs <project_dir> <output_dir>
#
# The output is copied OUT of the project into a named directory rather than
# left in impl_1. The 2022.1 bitstream is the only artifact ever verified on
# hardware, so the new one must not be able to overwrite it, and the two must be
# trivially distinguishable afterwards.
# =============================================================================

set script_dir [file dirname [file normalize [info script]]]

if {[llength $argv] < 2} {
    puts "ERROR: usage: build_2025_2.tcl <project_dir> <output_dir>"
    exit 1
}
set project_dir [file normalize [lindex $argv 0]]
set output_dir  [file normalize [lindex $argv 1]]

set xprs [glob -nocomplain -directory $project_dir *.xpr]
if {[llength $xprs] != 1} {
    puts "ERROR: expected exactly one .xpr in $project_dir"
    exit 1
}
set xpr [lindex $xprs 0]

puts "BUILD_TOOL=[version -short]"
puts "BUILD_XPR=$xpr"
puts "BUILD_OUT=$output_dir"

open_project $xpr

set repo [file join [file dirname $project_dir] ip_repo_optimized]
if {[file exists $repo]} {
    set_property ip_repo_paths [list $repo] [current_project]
    update_ip_catalog -rebuild
}

# Report any still-locked IP before spending an hour building something that
# would only fail later.
set locked [get_ips -quiet -filter {IS_LOCKED == 1}]
puts "LOCKED_AT_BUILD_START=[llength $locked]"
if {[llength $locked] > 0} {
    foreach ip $locked { puts "  LOCKED=[get_property IPDEF $ip]" }
    puts "ABORTING: locked IP would make the bitstream unusable."
    close_project
    exit 2
}

puts "STEP=generating_bd_targets"
# Must run BEFORE reset_run/launch_runs. launch_runs regenerates the block
# design's products on demand, and doing that after the reset marks the run
# out-of-date again, so launching fails with "Run 'synth_1' needs to be reset
# before launching" no matter how many times it is reset first.
set bd_file [get_files -quiet */design_1.bd]
if {$bd_file ne ""} {
    generate_target all $bd_file
    puts "BD_TARGETS_GENERATED=$bd_file"
}

puts "STEP=synthesis"
reset_run synth_1
launch_runs synth_1 -jobs 8
wait_on_run synth_1
set synth_progress [get_property PROGRESS [get_runs synth_1]]
puts "SYNTH_PROGRESS=$synth_progress"
if {$synth_progress ne "100%"} {
    puts "SYNTH_STATUS=[get_property STATUS [get_runs synth_1]]"
    puts "ERROR: synthesis did not complete"
    close_project
    exit 3
}

puts "STEP=implementation"
launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
set impl_progress [get_property PROGRESS [get_runs impl_1]]
puts "IMPL_PROGRESS=$impl_progress"
if {$impl_progress ne "100%"} {
    puts "IMPL_STATUS=[get_property STATUS [get_runs impl_1]]"
    puts "ERROR: implementation did not complete"
    close_project
    exit 4
}

# Timing and utilisation, so the result can be compared with 2022.1 rather than
# merely assumed equivalent.
catch {
    open_run impl_1
    report_timing_summary -file [file join $output_dir timing_summary.rpt]
    report_utilization -file [file join $output_dir utilization.rpt]
    set wns [get_property SLACK [get_timing_paths -max_paths 1]]
    puts "WNS=$wns"
    close_design
} report_msg
puts "REPORT_MSG=$report_msg"

set impl_dir [get_property DIRECTORY [get_runs impl_1]]
set built_bit [file join $impl_dir design_1_wrapper.bit]
set built_hwh [file join $impl_dir design_1_wrapper.hwh]
puts "BUILT_BIT=$built_bit"
puts "BUILT_BIT_EXISTS=[file exists $built_bit]"

file mkdir $output_dir
if {[file exists $built_bit]} {
    file copy -force $built_bit [file join $output_dir intrusion_detection_optimized.bit]
    puts "COPIED_BIT=[file join $output_dir intrusion_detection_optimized.bit]"
}
if {[file exists $built_hwh]} {
    file copy -force $built_hwh [file join $output_dir intrusion_detection_optimized.hwh]
    puts "COPIED_HWH=[file join $output_dir intrusion_detection_optimized.hwh]"
}

close_project
puts "BUILD_DONE"
exit 0
