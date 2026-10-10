#include <ap_axi_sdata.h>
#include <ap_int.h>
#include <hls_stream.h>

#define FRAME_WIDTH 320
#define FRAME_HEIGHT 240

typedef ap_axiu<32, 0, 0, 0> pixel_t;

void morphology(
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

    ap_uint<8> line_buffer_0[FRAME_WIDTH];
    ap_uint<8> line_buffer_1[FRAME_WIDTH];
    #pragma HLS BIND_STORAGE variable=line_buffer_0 type=ram_t2p
    #pragma HLS BIND_STORAGE variable=line_buffer_1 type=ram_t2p

    ap_uint<8> window[3][3];
    #pragma HLS ARRAY_PARTITION variable=window complete dim=0

    const bool size_ok = (width == FRAME_WIDTH) && (height == FRAME_HEIGHT);

    for (int y = 0; y < FRAME_HEIGHT; ++y) {
        for (int x = 0; x < FRAME_WIDTH; ++x) {
            #pragma HLS PIPELINE II=1
            pixel_t input = in_stream.read();
            ap_uint<8> value = input.data.range(7, 0);

            window[0][0] = window[0][1];
            window[0][1] = window[0][2];
            window[1][0] = window[1][1];
            window[1][1] = window[1][2];
            window[2][0] = window[2][1];
            window[2][1] = window[2][2];

            window[0][2] = line_buffer_0[x];
            window[1][2] = line_buffer_1[x];
            window[2][2] = value;

            line_buffer_0[x] = line_buffer_1[x];
            line_buffer_1[x] = value;

            ap_uint<8> eroded = 255;
            for (int row = 0; row < 3; ++row) {
                #pragma HLS UNROLL
                for (int column = 0; column < 3; ++column) {
                    #pragma HLS UNROLL
                    if (window[row][column] != 255) {
                        eroded = 0;
                    }
                }
            }

            const bool valid = size_ok && (y >= 2) && (x >= 2);
            pixel_t output;
            output.data = valid ? eroded : ap_uint<8>(0);
            output.keep = -1;
            output.strb = -1;
            output.last = input.last;
            out_stream.write(output);
        }
    }
}
