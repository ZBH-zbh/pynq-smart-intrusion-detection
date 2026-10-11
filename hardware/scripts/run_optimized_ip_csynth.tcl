set script_dir [file dirname [file normalize [info script]]]
set source_dir [file normalize [file join $script_dir .. pl pl hls_optimized]]
set build_dir [file join $script_dir build optimized_csynth]

file mkdir $build_dir
cd $build_dir

foreach top {rgb2gray frame_diff threshold morphology} {
    open_project ${top}_optimized
    set_top $top
    add_files [file join $source_dir ${top}.cpp]

    open_solution solution1 -flow_target vivado
    set_part {xc7z020clg400-1}
    create_clock -period 10 -name default
    csynth_design

    close_solution
    close_project
}

exit
