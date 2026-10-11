set script_dir [file dirname [file normalize [info script]]]
set project_root [file join $script_dir build optimized_csynth]
set export_dir [file normalize [file join $script_dir .. pl pl vivado ip_repo_optimized]]

file mkdir $export_dir

foreach top {rgb2gray frame_diff threshold morphology} {
    cd $project_root
    open_project ${top}_optimized
    open_solution solution1

    set output_zip [file join $export_dir xilinx_com_hls_${top}_1_0.zip]
    export_design -format ip_catalog -rtl verilog -output $output_zip

    close_solution
    close_project
}

exit
