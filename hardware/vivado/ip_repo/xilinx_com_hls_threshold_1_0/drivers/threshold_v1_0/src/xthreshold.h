// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2022.1 (64-bit)
// Tool Version Limit: 2022.04
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// ==============================================================
#ifndef XTHRESHOLD_H
#define XTHRESHOLD_H

#ifdef __cplusplus
extern "C" {
#endif

/***************************** Include Files *********************************/
#ifndef __linux__
#include "xil_types.h"
#include "xil_assert.h"
#include "xstatus.h"
#include "xil_io.h"
#else
#include <stdint.h>
#include <assert.h>
#include <dirent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <stddef.h>
#endif
#include "xthreshold_hw.h"

/**************************** Type Definitions ******************************/
#ifdef __linux__
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
#else
typedef struct {
    u16 DeviceId;
    u64 Ctrl_BaseAddress;
} XThreshold_Config;
#endif

typedef struct {
    u64 Ctrl_BaseAddress;
    u32 IsReady;
} XThreshold;

typedef u32 word_type;

/***************** Macros (Inline Functions) Definitions *********************/
#ifndef __linux__
#define XThreshold_WriteReg(BaseAddress, RegOffset, Data) \
    Xil_Out32((BaseAddress) + (RegOffset), (u32)(Data))
#define XThreshold_ReadReg(BaseAddress, RegOffset) \
    Xil_In32((BaseAddress) + (RegOffset))
#else
#define XThreshold_WriteReg(BaseAddress, RegOffset, Data) \
    *(volatile u32*)((BaseAddress) + (RegOffset)) = (u32)(Data)
#define XThreshold_ReadReg(BaseAddress, RegOffset) \
    *(volatile u32*)((BaseAddress) + (RegOffset))

#define Xil_AssertVoid(expr)    assert(expr)
#define Xil_AssertNonvoid(expr) assert(expr)

#define XST_SUCCESS             0
#define XST_DEVICE_NOT_FOUND    2
#define XST_OPEN_DEVICE_FAILED  3
#define XIL_COMPONENT_IS_READY  1
#endif

/************************** Function Prototypes *****************************/
#ifndef __linux__
int XThreshold_Initialize(XThreshold *InstancePtr, u16 DeviceId);
XThreshold_Config* XThreshold_LookupConfig(u16 DeviceId);
int XThreshold_CfgInitialize(XThreshold *InstancePtr, XThreshold_Config *ConfigPtr);
#else
int XThreshold_Initialize(XThreshold *InstancePtr, const char* InstanceName);
int XThreshold_Release(XThreshold *InstancePtr);
#endif

void XThreshold_Start(XThreshold *InstancePtr);
u32 XThreshold_IsDone(XThreshold *InstancePtr);
u32 XThreshold_IsIdle(XThreshold *InstancePtr);
u32 XThreshold_IsReady(XThreshold *InstancePtr);
void XThreshold_EnableAutoRestart(XThreshold *InstancePtr);
void XThreshold_DisableAutoRestart(XThreshold *InstancePtr);

void XThreshold_Set_width(XThreshold *InstancePtr, u32 Data);
u32 XThreshold_Get_width(XThreshold *InstancePtr);
void XThreshold_Set_height(XThreshold *InstancePtr, u32 Data);
u32 XThreshold_Get_height(XThreshold *InstancePtr);
void XThreshold_Set_thresh(XThreshold *InstancePtr, u32 Data);
u32 XThreshold_Get_thresh(XThreshold *InstancePtr);

void XThreshold_InterruptGlobalEnable(XThreshold *InstancePtr);
void XThreshold_InterruptGlobalDisable(XThreshold *InstancePtr);
void XThreshold_InterruptEnable(XThreshold *InstancePtr, u32 Mask);
void XThreshold_InterruptDisable(XThreshold *InstancePtr, u32 Mask);
void XThreshold_InterruptClear(XThreshold *InstancePtr, u32 Mask);
u32 XThreshold_InterruptGetEnabled(XThreshold *InstancePtr);
u32 XThreshold_InterruptGetStatus(XThreshold *InstancePtr);

#ifdef __cplusplus
}
#endif

#endif
