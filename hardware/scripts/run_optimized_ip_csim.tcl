set script_dir [file dirname [file normalize [info script]]]
set source_dir [file normalize [file join $script_dir .. pl pl hls_optimized]]
set build_dir [file join $script_dir build]

file mkdir $build_dir
cd $build_dir
open_project optimized_ip_csim
set_top rgb2gray
add_files [file join $source_dir rgb2gray.cpp]
add_files [file join $source_dir frame_diff.cpp]
add_files [file join $source_dir threshold.cpp]
add_files [file join $source_dir morphology.cpp]
add_files -tb [file join $script_dir test_motion_ips_optimized.cpp]

open_solution solution1 -flow_target vivado
set_part {xc7z020clg400-1}
create_clock -period 10 -name default
csim_design
exit
