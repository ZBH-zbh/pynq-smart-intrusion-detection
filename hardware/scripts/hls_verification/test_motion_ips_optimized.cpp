#include <ap_axi_sdata.h>
#include <ap_int.h>
#include <hls_stream.h>

#include <iostream>


#define FRAME_WIDTH 320
#define FRAME_HEIGHT 240
#define FRAME_PIXELS 76800

typedef ap_axiu<32, 0, 0, 0> pixel_t;

void rgb2gray(
    hls::stream<pixel_t>& in_stream,
    hls::stream<pixel_t>& out_stream,
    int width,
    int height
);
void frame_diff(
    hls::stream<pixel_t>& in_stream,
    hls::stream<pixel_t>& out_stream,
    int width,
    int height
);
void threshold(
    hls::stream<pixel_t>& in_stream,
    hls::stream<pixel_t>& out_stream,
    int width,
    int height,
    int thresh
);
void morphology(
    hls::stream<pixel_t>& in_stream,
    hls::stream<pixel_t>& out_stream,
    int width,
    int height
);


static pixel_t make_pixel(unsigned int value, int index) {
    pixel_t pixel;
    pixel.data = value;
    pixel.keep = -1;
    pixel.strb = -1;
    pixel.last = (index == FRAME_PIXELS - 1) ? 1 : 0;
    return pixel;
}


static bool check_pixel(
    const char* test_name,
    int index,
    const pixel_t& actual,
    unsigned int expected_data
) {
    const unsigned int actual_data = actual.data.to_uint();
    const bool actual_last = actual.last.to_uint() != 0;
    const bool expected_last = index == FRAME_PIXELS - 1;
    if (actual_data == expected_data && actual_last == expected_last) {
        return true;
    }
    std::cerr << test_name << " failed at pixel " << index
              << ": data=" << actual_data
              << " expected=" << expected_data
              << ", last=" << actual_last
              << " expected_last=" << expected_last << std::endl;
    return false;
}


static bool test_rgb2gray() {
    hls::stream<pixel_t> input;
    hls::stream<pixel_t> output;
    for (int i = 0; i < FRAME_PIXELS; ++i) {
        const unsigned int red = (i * 3) & 0xFF;
        const unsigned int green = (i * 5 + 1) & 0xFF;
        const unsigned int blue = (i * 7 + 2) & 0xFF;
        input.write(make_pixel((red << 16) | (green << 8) | blue, i));
    }
    rgb2gray(input, output, FRAME_WIDTH, FRAME_HEIGHT);

    bool passed = true;
    for (int i = 0; i < FRAME_PIXELS; ++i) {
        const unsigned int red = (i * 3) & 0xFF;
        const unsigned int green = (i * 5 + 1) & 0xFF;
        const unsigned int blue = (i * 7 + 2) & 0xFF;
        const unsigned int expected =
            (red * 76 + green * 150 + blue * 29) >> 8;
        passed &= check_pixel("rgb2gray", i, output.read(), expected);
    }
    return passed;
}


static bool test_threshold() {
    hls::stream<pixel_t> input;
    hls::stream<pixel_t> output;
    for (int i = 0; i < FRAME_PIXELS; ++i) {
        input.write(make_pixel(i & 0x3F, i));
    }
    threshold(input, output, FRAME_WIDTH, FRAME_HEIGHT, 30);

    bool passed = true;
    for (int i = 0; i < FRAME_PIXELS; ++i) {
        const unsigned int expected = ((i & 0x3F) > 30) ? 255 : 0;
        passed &= check_pixel("threshold", i, output.read(), expected);
    }
    return passed;
}


static bool test_invalid_size_is_zeroed() {
    hls::stream<pixel_t> input;
    hls::stream<pixel_t> output;
    for (int i = 0; i < FRAME_PIXELS; ++i) {
        input.write(make_pixel(255, i));
    }
    threshold(input, output, FRAME_WIDTH - 1, FRAME_HEIGHT, 30);

    bool passed = true;
    for (int i = 0; i < FRAME_PIXELS; ++i) {
        passed &= check_pixel("invalid size", i, output.read(), 0);
    }
    return passed;
}


static bool test_frame_diff() {
    hls::stream<pixel_t> input;
    hls::stream<pixel_t> output;
    for (int i = 0; i < FRAME_PIXELS; ++i) {
        input.write(make_pixel((i * 3) & 0xFF, i));
    }
    frame_diff(input, output, FRAME_WIDTH, FRAME_HEIGHT);

    bool passed = true;
    for (int i = 0; i < FRAME_PIXELS; ++i) {
        passed &= check_pixel("frame_diff first", i, output.read(), (i * 3) & 0xFF);
    }

    for (int i = 0; i < FRAME_PIXELS; ++i) {
        input.write(make_pixel((i * 3 + 5) & 0xFF, i));
    }
    frame_diff(input, output, FRAME_WIDTH, FRAME_HEIGHT);
    for (int i = 0; i < FRAME_PIXELS; ++i) {
        const unsigned int previous = (i * 3) & 0xFF;
        const unsigned int current = (i * 3 + 5) & 0xFF;
        const unsigned int expected =
            (current > previous) ? current - previous : previous - current;
        passed &= check_pixel("frame_diff second", i, output.read(), expected);
    }
    return passed;
}


static bool test_morphology() {
    hls::stream<pixel_t> input;
    hls::stream<pixel_t> output;
    const int zero_x = 100;
    const int zero_y = 100;
    for (int i = 0; i < FRAME_PIXELS; ++i) {
        const int x = i % FRAME_WIDTH;
        const int y = i / FRAME_WIDTH;
        const unsigned int value = (x == zero_x && y == zero_y) ? 0 : 255;
        input.write(make_pixel(value, i));
    }
    morphology(input, output, FRAME_WIDTH, FRAME_HEIGHT);

    bool passed = true;
    for (int i = 0; i < FRAME_PIXELS; ++i) {
        const int x = i % FRAME_WIDTH;
        const int y = i / FRAME_WIDTH;
        const bool border = x < 2 || y < 2;
        const bool zero_in_window =
            x >= zero_x && x <= zero_x + 2 &&
            y >= zero_y && y <= zero_y + 2;
        const unsigned int expected = (border || zero_in_window) ? 0 : 255;
        passed &= check_pixel("morphology", i, output.read(), expected);
    }
    return passed;
}


int main() {
    bool passed = true;
    passed &= test_rgb2gray();
    passed &= test_threshold();
    passed &= test_invalid_size_is_zeroed();
    passed &= test_frame_diff();
    passed &= test_morphology();

    if (!passed) {
        std::cerr << "One or more optimized HLS C tests failed." << std::endl;
        return 1;
    }
    std::cout << "All optimized HLS IP C tests passed." << std::endl;
    return 0;
}
