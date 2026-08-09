#include <jni.h>
#include <android/asset_manager.h>
#include <android/asset_manager_jni.h>
#include <vulkan/vulkan.h>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

void check(VkResult result, const char* what) {
    if (result != VK_SUCCESS) {
        throw std::runtime_error(std::string(what) + " failed with VkResult=" + std::to_string(result));
    }
}

struct DeviceContext {
    VkInstance instance = VK_NULL_HANDLE;
    VkPhysicalDevice physical = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkQueue queue = VK_NULL_HANDLE;
    uint32_t queueFamily = UINT32_MAX;
};

void destroyDeviceContext(DeviceContext& ctx) {
    if (ctx.device != VK_NULL_HANDLE) vkDestroyDevice(ctx.device, nullptr);
    if (ctx.instance != VK_NULL_HANDLE) vkDestroyInstance(ctx.instance, nullptr);
    ctx = {};
}

DeviceContext createDeviceContext() {
    DeviceContext ctx;
    VkApplicationInfo appInfo{VK_STRUCTURE_TYPE_APPLICATION_INFO};
    appInfo.pApplicationName = "ComputeSwarm";
    appInfo.applicationVersion = VK_MAKE_VERSION(0, 4, 0);
    appInfo.pEngineName = "ComputeSwarm";
    appInfo.engineVersion = VK_MAKE_VERSION(0, 4, 0);
    appInfo.apiVersion = VK_API_VERSION_1_0;

    VkInstanceCreateInfo instanceInfo{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    instanceInfo.pApplicationInfo = &appInfo;
    check(vkCreateInstance(&instanceInfo, nullptr, &ctx.instance), "vkCreateInstance");

    uint32_t deviceCount = 0;
    check(vkEnumeratePhysicalDevices(ctx.instance, &deviceCount, nullptr), "vkEnumeratePhysicalDevices(count)");
    if (deviceCount == 0) throw std::runtime_error("no Vulkan physical device");
    std::vector<VkPhysicalDevice> devices(deviceCount);
    check(vkEnumeratePhysicalDevices(ctx.instance, &deviceCount, devices.data()), "vkEnumeratePhysicalDevices");

    for (VkPhysicalDevice physical : devices) {
        uint32_t queueCount = 0;
        vkGetPhysicalDeviceQueueFamilyProperties(physical, &queueCount, nullptr);
        std::vector<VkQueueFamilyProperties> queues(queueCount);
        vkGetPhysicalDeviceQueueFamilyProperties(physical, &queueCount, queues.data());
        for (uint32_t i = 0; i < queueCount; ++i) {
            if ((queues[i].queueFlags & VK_QUEUE_COMPUTE_BIT) != 0) {
                ctx.physical = physical;
                ctx.queueFamily = i;
                break;
            }
        }
        if (ctx.physical != VK_NULL_HANDLE) break;
    }
    if (ctx.physical == VK_NULL_HANDLE) throw std::runtime_error("no Vulkan compute queue");

    const float priority = 1.0f;
    VkDeviceQueueCreateInfo queueInfo{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
    queueInfo.queueFamilyIndex = ctx.queueFamily;
    queueInfo.queueCount = 1;
    queueInfo.pQueuePriorities = &priority;

    VkDeviceCreateInfo deviceInfo{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
    deviceInfo.queueCreateInfoCount = 1;
    deviceInfo.pQueueCreateInfos = &queueInfo;
    check(vkCreateDevice(ctx.physical, &deviceInfo, nullptr, &ctx.device), "vkCreateDevice");
    vkGetDeviceQueue(ctx.device, ctx.queueFamily, 0, &ctx.queue);
    return ctx;
}

uint32_t findMemoryType(VkPhysicalDevice physical, uint32_t typeBits, VkMemoryPropertyFlags required) {
    VkPhysicalDeviceMemoryProperties props{};
    vkGetPhysicalDeviceMemoryProperties(physical, &props);
    for (uint32_t i = 0; i < props.memoryTypeCount; ++i) {
        if ((typeBits & (1u << i)) != 0 && (props.memoryTypes[i].propertyFlags & required) == required) {
            return i;
        }
    }
    throw std::runtime_error("no compatible Vulkan memory type");
}

struct Buffer {
    VkBuffer buffer = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    void* mapped = nullptr;
};

Buffer createStorageBuffer(DeviceContext& ctx, VkDeviceSize size) {
    Buffer result;
    VkBufferCreateInfo info{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    info.size = size;
    info.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
    info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    check(vkCreateBuffer(ctx.device, &info, nullptr, &result.buffer), "vkCreateBuffer");

    VkMemoryRequirements requirements{};
    vkGetBufferMemoryRequirements(ctx.device, result.buffer, &requirements);
    VkMemoryAllocateInfo alloc{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    alloc.allocationSize = requirements.size;
    alloc.memoryTypeIndex = findMemoryType(
        ctx.physical,
        requirements.memoryTypeBits,
        VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT
    );
    check(vkAllocateMemory(ctx.device, &alloc, nullptr, &result.memory), "vkAllocateMemory");
    check(vkBindBufferMemory(ctx.device, result.buffer, result.memory, 0), "vkBindBufferMemory");
    check(vkMapMemory(ctx.device, result.memory, 0, size, 0, &result.mapped), "vkMapMemory");
    return result;
}

void destroyBuffer(VkDevice device, Buffer& buffer) {
    if (buffer.mapped != nullptr && buffer.memory != VK_NULL_HANDLE) vkUnmapMemory(device, buffer.memory);
    if (buffer.buffer != VK_NULL_HANDLE) vkDestroyBuffer(device, buffer.buffer, nullptr);
    if (buffer.memory != VK_NULL_HANDLE) vkFreeMemory(device, buffer.memory, nullptr);
    buffer = {};
}

std::vector<uint32_t> readShader(AAssetManager* manager) {
    if (manager == nullptr) throw std::runtime_error("Android AssetManager unavailable");
    AAsset* asset = AAssetManager_open(manager, "shaders/vector_add.comp.spv", AASSET_MODE_BUFFER);
    if (asset == nullptr) throw std::runtime_error("compiled Vulkan shader asset not found");
    const off_t byteLength = AAsset_getLength(asset);
    if (byteLength <= 0 || (byteLength % 4) != 0) {
        AAsset_close(asset);
        throw std::runtime_error("invalid SPIR-V shader size");
    }
    std::vector<uint32_t> words(static_cast<size_t>(byteLength) / sizeof(uint32_t));
    const int read = AAsset_read(asset, words.data(), static_cast<size_t>(byteLength));
    AAsset_close(asset);
    if (read != byteLength) throw std::runtime_error("failed to read SPIR-V shader asset");
    return words;
}

void throwJava(JNIEnv* env, const std::exception& error) {
    jclass cls = env->FindClass("java/lang/RuntimeException");
    if (cls != nullptr) env->ThrowNew(cls, error.what());
}

}  // namespace

extern "C" JNIEXPORT jboolean JNICALL
Java_com_camalabs_computeswarm_VulkanBackend_nativeIsAvailable(JNIEnv* env, jobject) {
    try {
        DeviceContext ctx = createDeviceContext();
        destroyDeviceContext(ctx);
        return JNI_TRUE;
    } catch (const std::exception&) {
        return JNI_FALSE;
    }
}

extern "C" JNIEXPORT jfloatArray JNICALL
Java_com_camalabs_computeswarm_VulkanBackend_nativeVectorAdd(
    JNIEnv* env,
    jobject,
    jobject assetManagerObject,
    jfloatArray aArray,
    jfloatArray bArray
) {
    DeviceContext ctx;
    Buffer aBuffer, bBuffer, cBuffer;
    VkShaderModule shader = VK_NULL_HANDLE;
    VkDescriptorSetLayout descriptorLayout = VK_NULL_HANDLE;
    VkPipelineLayout pipelineLayout = VK_NULL_HANDLE;
    VkPipeline pipeline = VK_NULL_HANDLE;
    VkDescriptorPool descriptorPool = VK_NULL_HANDLE;
    VkCommandPool commandPool = VK_NULL_HANDLE;

    try {
        if (aArray == nullptr || bArray == nullptr) throw std::runtime_error("vectors must not be null");
        const jsize count = env->GetArrayLength(aArray);
        if (count <= 0 || env->GetArrayLength(bArray) != count) {
            throw std::runtime_error("vectors must be non-empty and have identical lengths");
        }
        const VkDeviceSize byteSize = static_cast<VkDeviceSize>(count) * sizeof(float);
        ctx = createDeviceContext();
        aBuffer = createStorageBuffer(ctx, byteSize);
        bBuffer = createStorageBuffer(ctx, byteSize);
        cBuffer = createStorageBuffer(ctx, byteSize);
        env->GetFloatArrayRegion(aArray, 0, count, static_cast<jfloat*>(aBuffer.mapped));
        env->GetFloatArrayRegion(bArray, 0, count, static_cast<jfloat*>(bBuffer.mapped));
        std::memset(cBuffer.mapped, 0, static_cast<size_t>(byteSize));

        AAssetManager* assetManager = AAssetManager_fromJava(env, assetManagerObject);
        const std::vector<uint32_t> shaderWords = readShader(assetManager);
        VkShaderModuleCreateInfo shaderInfo{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
        shaderInfo.codeSize = shaderWords.size() * sizeof(uint32_t);
        shaderInfo.pCode = shaderWords.data();
        check(vkCreateShaderModule(ctx.device, &shaderInfo, nullptr, &shader), "vkCreateShaderModule");

        VkDescriptorSetLayoutBinding bindings[3]{};
        for (uint32_t i = 0; i < 3; ++i) {
            bindings[i].binding = i;
            bindings[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
            bindings[i].descriptorCount = 1;
            bindings[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
        }
        VkDescriptorSetLayoutCreateInfo descriptorInfo{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
        descriptorInfo.bindingCount = 3;
        descriptorInfo.pBindings = bindings;
        check(vkCreateDescriptorSetLayout(ctx.device, &descriptorInfo, nullptr, &descriptorLayout), "vkCreateDescriptorSetLayout");

        VkPushConstantRange pushRange{};
        pushRange.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
        pushRange.offset = 0;
        pushRange.size = sizeof(uint32_t);
        VkPipelineLayoutCreateInfo layoutInfo{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
        layoutInfo.setLayoutCount = 1;
        layoutInfo.pSetLayouts = &descriptorLayout;
        layoutInfo.pushConstantRangeCount = 1;
        layoutInfo.pPushConstantRanges = &pushRange;
        check(vkCreatePipelineLayout(ctx.device, &layoutInfo, nullptr, &pipelineLayout), "vkCreatePipelineLayout");

        VkPipelineShaderStageCreateInfo stage{VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO};
        stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
        stage.module = shader;
        stage.pName = "main";
        VkComputePipelineCreateInfo pipelineInfo{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
        pipelineInfo.stage = stage;
        pipelineInfo.layout = pipelineLayout;
        check(vkCreateComputePipelines(ctx.device, VK_NULL_HANDLE, 1, &pipelineInfo, nullptr, &pipeline), "vkCreateComputePipelines");

        VkDescriptorPoolSize poolSize{};
        poolSize.type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        poolSize.descriptorCount = 3;
        VkDescriptorPoolCreateInfo poolInfo{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
        poolInfo.maxSets = 1;
        poolInfo.poolSizeCount = 1;
        poolInfo.pPoolSizes = &poolSize;
        check(vkCreateDescriptorPool(ctx.device, &poolInfo, nullptr, &descriptorPool), "vkCreateDescriptorPool");

        VkDescriptorSetAllocateInfo setInfo{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
        setInfo.descriptorPool = descriptorPool;
        setInfo.descriptorSetCount = 1;
        setInfo.pSetLayouts = &descriptorLayout;
        VkDescriptorSet descriptorSet = VK_NULL_HANDLE;
        check(vkAllocateDescriptorSets(ctx.device, &setInfo, &descriptorSet), "vkAllocateDescriptorSets");

        VkDescriptorBufferInfo bufferInfos[3]{};
        bufferInfos[0] = {aBuffer.buffer, 0, byteSize};
        bufferInfos[1] = {bBuffer.buffer, 0, byteSize};
        bufferInfos[2] = {cBuffer.buffer, 0, byteSize};
        VkWriteDescriptorSet writes[3]{};
        for (uint32_t i = 0; i < 3; ++i) {
            writes[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
            writes[i].dstSet = descriptorSet;
            writes[i].dstBinding = i;
            writes[i].descriptorCount = 1;
            writes[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
            writes[i].pBufferInfo = &bufferInfos[i];
        }
        vkUpdateDescriptorSets(ctx.device, 3, writes, 0, nullptr);

        VkCommandPoolCreateInfo commandPoolInfo{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
        commandPoolInfo.queueFamilyIndex = ctx.queueFamily;
        commandPoolInfo.flags = VK_COMMAND_POOL_CREATE_TRANSIENT_BIT;
        check(vkCreateCommandPool(ctx.device, &commandPoolInfo, nullptr, &commandPool), "vkCreateCommandPool");
        VkCommandBufferAllocateInfo commandAlloc{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
        commandAlloc.commandPool = commandPool;
        commandAlloc.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
        commandAlloc.commandBufferCount = 1;
        VkCommandBuffer command = VK_NULL_HANDLE;
        check(vkAllocateCommandBuffers(ctx.device, &commandAlloc, &command), "vkAllocateCommandBuffers");

        VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
        begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
        check(vkBeginCommandBuffer(command, &begin), "vkBeginCommandBuffer");
        vkCmdBindPipeline(command, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
        vkCmdBindDescriptorSets(command, VK_PIPELINE_BIND_POINT_COMPUTE, pipelineLayout, 0, 1, &descriptorSet, 0, nullptr);
        const uint32_t elementCount = static_cast<uint32_t>(count);
        vkCmdPushConstants(command, pipelineLayout, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(uint32_t), &elementCount);
        vkCmdDispatch(command, (elementCount + 255u) / 256u, 1, 1);

        VkMemoryBarrier barrier{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
        barrier.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
        barrier.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
        vkCmdPipelineBarrier(
            command,
            VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
            VK_PIPELINE_STAGE_HOST_BIT,
            0,
            1,
            &barrier,
            0,
            nullptr,
            0,
            nullptr
        );
        check(vkEndCommandBuffer(command), "vkEndCommandBuffer");
        VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};
        submit.commandBufferCount = 1;
        submit.pCommandBuffers = &command;
        check(vkQueueSubmit(ctx.queue, 1, &submit, VK_NULL_HANDLE), "vkQueueSubmit");
        check(vkQueueWaitIdle(ctx.queue), "vkQueueWaitIdle");

        jfloatArray result = env->NewFloatArray(count);
        if (result == nullptr) throw std::runtime_error("failed to allocate Java output array");
        env->SetFloatArrayRegion(result, 0, count, static_cast<jfloat*>(cBuffer.mapped));

        vkDestroyCommandPool(ctx.device, commandPool, nullptr); commandPool = VK_NULL_HANDLE;
        vkDestroyDescriptorPool(ctx.device, descriptorPool, nullptr); descriptorPool = VK_NULL_HANDLE;
        vkDestroyPipeline(ctx.device, pipeline, nullptr); pipeline = VK_NULL_HANDLE;
        vkDestroyPipelineLayout(ctx.device, pipelineLayout, nullptr); pipelineLayout = VK_NULL_HANDLE;
        vkDestroyDescriptorSetLayout(ctx.device, descriptorLayout, nullptr); descriptorLayout = VK_NULL_HANDLE;
        vkDestroyShaderModule(ctx.device, shader, nullptr); shader = VK_NULL_HANDLE;
        destroyBuffer(ctx.device, cBuffer);
        destroyBuffer(ctx.device, bBuffer);
        destroyBuffer(ctx.device, aBuffer);
        destroyDeviceContext(ctx);
        return result;
    } catch (const std::exception& error) {
        if (ctx.device != VK_NULL_HANDLE) {
            vkDeviceWaitIdle(ctx.device);
            if (commandPool != VK_NULL_HANDLE) vkDestroyCommandPool(ctx.device, commandPool, nullptr);
            if (descriptorPool != VK_NULL_HANDLE) vkDestroyDescriptorPool(ctx.device, descriptorPool, nullptr);
            if (pipeline != VK_NULL_HANDLE) vkDestroyPipeline(ctx.device, pipeline, nullptr);
            if (pipelineLayout != VK_NULL_HANDLE) vkDestroyPipelineLayout(ctx.device, pipelineLayout, nullptr);
            if (descriptorLayout != VK_NULL_HANDLE) vkDestroyDescriptorSetLayout(ctx.device, descriptorLayout, nullptr);
            if (shader != VK_NULL_HANDLE) vkDestroyShaderModule(ctx.device, shader, nullptr);
            destroyBuffer(ctx.device, cBuffer);
            destroyBuffer(ctx.device, bBuffer);
            destroyBuffer(ctx.device, aBuffer);
        }
        destroyDeviceContext(ctx);
        throwJava(env, error);
        return nullptr;
    }
}
