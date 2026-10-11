#include <ap_axi_sdata.h>
#include <ap_int.h>
#include <hls_stream.h>

#include <iostream>


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


static pixel_t make_pixel(unsigned int value, bool last) {
    pixel_t pixel;
    pixel.data = value;
    pixel.keep = -1;
    pixel.strb = -1;
    pixel.last = last ? 1 : 0;
    return pixel;
}


static bool check_pixel(
    const char* test_name,
    int index,
    const pixel_t& actual,
    unsigned int expected_data,
    bool expected_last
) {
    const unsigned int actual_data = actual.data.to_uint();
    const bool actual_last = actual.last.to_uint() != 0;
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
    const unsigned int colors[4] = {
        0x00FF0000,
        0x0000FF00,
        0x000000FF,
        0x00123456,
    };

    for (int i = 0; i < 4; ++i) {
        input.write(make_pixel(colors[i], i == 3));
    }
    rgb2gray(input, output, 4, 1);

    bool passed = true;
    for (int i = 0; i < 4; ++i) {
        const unsigned int red = (colors[i] >> 16) & 0xFF;
        const unsigned int green = (colors[i] >> 8) & 0xFF;
        const unsigned int blue = colors[i] & 0xFF;
        const unsigned int expected =
            (red * 76 + green * 150 + blue * 29) >> 8;
        passed &= check_pixel("rgb2gray", i, output.read(), expected, i == 3);
    }
    return passed;
}


static bool test_threshold() {
    hls::stream<pixel_t> input;
    hls::stream<pixel_t> output;
    const unsigned int values[4] = {29, 30, 31, 255};
    const unsigned int expected[4] = {0, 0, 255, 255};

    for (int i = 0; i < 4; ++i) {
        input.write(make_pixel(values[i], i == 3));
    }
    threshold(input, output, 4, 1, 30);

    bool passed = true;
    for (int i = 0; i < 4; ++i) {
        passed &= check_pixel("threshold", i, output.read(), expected[i], i == 3);
    }
    return passed;
}


static bool test_frame_diff() {
    hls::stream<pixel_t> input;
    hls::stream<pixel_t> output;
    const unsigned int first[4] = {10, 20, 30, 40};
    const unsigned int second[4] = {13, 18, 35, 40};
    const unsigned int expected_second[4] = {3, 2, 5, 0};

    for (int i = 0; i < 4; ++i) {
        input.write(make_pixel(first[i], i == 3));
    }
    frame_diff(input, output, 4, 1);

    bool passed = true;
    for (int i = 0; i < 4; ++i) {
        passed &= check_pixel("frame_diff first", i, output.read(), first[i], i == 3);
    }

    for (int i = 0; i < 4; ++i) {
        input.write(make_pixel(second[i], i == 3));
    }
    frame_diff(input, output, 4, 1);
    for (int i = 0; i < 4; ++i) {
        passed &= check_pixel(
            "frame_diff second", i, output.read(), expected_second[i], i == 3
        );
    }
    return passed;
}


static bool test_morphology() {
    hls::stream<pixel_t> input;
    hls::stream<pixel_t> output;
    const int width = 5;
    const int height = 5;
    const int total = width * height;

    for (int i = 0; i < total; ++i) {
        input.write(make_pixel(255, i == total - 1));
    }
    morphology(input, output, width, height);

    bool passed = true;
    for (int i = 0; i < total; ++i) {
        const int x = i % width;
        const int y = i / width;
        const unsigned int expected = (x >= 2 && y >= 2) ? 255 : 0;
        passed &= check_pixel(
            "morphology", i, output.read(), expected, i == total - 1
        );
    }
    return passed;
}


int main() {
    bool passed = true;
    passed &= test_rgb2gray();
    passed &= test_threshold();
    passed &= test_frame_diff();
    passed &= test_morphology();

    if (!passed) {
        std::cerr << "One or more HLS C tests failed." << std::endl;
        return 1;
    }
    std::cout << "All current HLS IP C tests passed." << std::endl;
    return 0;
}
