#include <ap_axi_sdata.h>
#include <ap_int.h>
#include <hls_stream.h>

#define FRAME_WIDTH 320
#define FRAME_HEIGHT 240
#define FRAME_PIXELS 76800

typedef ap_axiu<32, 0, 0, 0> pixel_t;

void frame_diff(
    hls::stream<pixel_t>& in_stream,
    hls::stream<pixel_t>& out_stream,
    int width,
    int height
) {
    #pragma HLS INTERFACE axis port=in_stream
    #pragma HLS INTERFACE axis port=out_stream
    #pragma HLS INTERFACE s_axilite port=width bundle=CTRL
    #pragma HLS INTERFACE s_axilite port=height bundle=CTRL
    #pragma HLS INTERFACE s_axilite port=return bundle=CTRL

    static ap_uint<8> previous_frame[FRAME_PIXELS];
    #pragma HLS BIND_STORAGE variable=previous_frame type=ram_t2p

    const bool size_ok = (width == FRAME_WIDTH) && (height == FRAME_HEIGHT);

    for (int i = 0; i < FRAME_PIXELS; ++i) {
        #pragma HLS PIPELINE II=1
        pixel_t input = in_stream.read();
        ap_uint<8> current = input.data.range(7, 0);
        ap_uint<8> previous = previous_frame[i];
        ap_uint<8> difference =
            (current > previous) ? (current - previous) : (previous - current);

        pixel_t output;
        output.data = size_ok ? difference : ap_uint<8>(0);
        output.keep = -1;
        output.strb = -1;
        output.last = input.last;
        out_stream.write(output);

        if (size_ok) {
            previous_frame[i] = current;
        }
    }
}
