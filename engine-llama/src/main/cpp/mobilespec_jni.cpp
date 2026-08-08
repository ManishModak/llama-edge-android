#include <jni.h>

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include "ggml-backend.h"
#include "ggml.h"
#include "llama.h"

namespace {

struct Session {
    llama_model * model = nullptr;
    llama_context * context = nullptr;
    const llama_vocab * vocab = nullptr;
    std::atomic_bool cancelled{false};
    std::mutex generation_mutex;
    int baseline_decode_threads = 0;
    int baseline_prefill_threads = 0;
    int optimized_decode_threads = 0;
    int optimized_prefill_threads = 0;

    ~Session() {
        if (context != nullptr) {
            llama_free(context);
        }
        if (model != nullptr) {
            llama_model_free(model);
        }
    }
};

std::once_flag backend_once;

void initialize_backend() {
    std::call_once(backend_once, [] {
        llama_backend_init();
        ggml_backend_load_all();
    });
}

void throw_illegal_state(JNIEnv * env, const std::string & message) {
    const jclass error_class = env->FindClass("java/lang/IllegalStateException");
    if (error_class != nullptr) {
        env->ThrowNew(error_class, message.c_str());
    }
}

std::string from_jstring(JNIEnv * env, jstring value) {
    if (value == nullptr) {
        return {};
    }

    const jclass string_class = env->FindClass("java/lang/String");
    const jmethodID get_bytes = string_class == nullptr
        ? nullptr
        : env->GetMethodID(string_class, "getBytes", "(Ljava/lang/String;)[B");
    const jstring utf8_name = env->NewStringUTF("UTF-8");
    if (get_bytes == nullptr || utf8_name == nullptr) {
        return {};
    }

    auto bytes = static_cast<jbyteArray>(
        env->CallObjectMethod(value, get_bytes, utf8_name));
    env->DeleteLocalRef(utf8_name);
    env->DeleteLocalRef(string_class);
    if (bytes == nullptr || env->ExceptionCheck()) {
        return {};
    }

    const jsize size = env->GetArrayLength(bytes);
    std::string result(static_cast<size_t>(size), '\0');
    if (size > 0) {
        env->GetByteArrayRegion(
            bytes,
            0,
            size,
            reinterpret_cast<jbyte *>(result.data()));
    }
    env->DeleteLocalRef(bytes);
    return result;
}

Session * from_handle(jlong handle) {
    return reinterpret_cast<Session *>(static_cast<intptr_t>(handle));
}

std::string token_piece(const llama_vocab * vocab, llama_token token) {
    std::vector<char> buffer(256);
    int count = llama_token_to_piece(
        vocab,
        token,
        buffer.data(),
        static_cast<int32_t>(buffer.size()),
        0,
        true);
    if (count < 0) {
        buffer.resize(static_cast<size_t>(-count));
        count = llama_token_to_piece(
            vocab,
            token,
            buffer.data(),
            static_cast<int32_t>(buffer.size()),
            0,
            true);
    }
    return count > 0 ? std::string(buffer.data(), static_cast<size_t>(count)) : std::string();
}

jstring new_utf8_string(
        JNIEnv * env,
        const std::string & bytes,
        jclass string_class,
        jmethodID string_constructor,
        jstring charset_name) {
    jbyteArray byte_array = env->NewByteArray(static_cast<jsize>(bytes.size()));
    if (byte_array == nullptr) {
        return nullptr;
    }
    if (!bytes.empty()) {
        env->SetByteArrayRegion(
            byte_array,
            0,
            static_cast<jsize>(bytes.size()),
            reinterpret_cast<const jbyte *>(bytes.data()));
    }
    auto result = static_cast<jstring>(
        env->NewObject(string_class, string_constructor, byte_array, charset_name));
    env->DeleteLocalRef(byte_array);
    return result;
}

size_t complete_utf8_prefix_size(const std::string & bytes) {
    size_t offset = 0;
    while (offset < bytes.size()) {
        const auto first = static_cast<unsigned char>(bytes[offset]);
        size_t sequence_size = 1;
        if ((first & 0x80U) == 0) {
            sequence_size = 1;
        } else if ((first & 0xE0U) == 0xC0U) {
            sequence_size = 2;
        } else if ((first & 0xF0U) == 0xE0U) {
            sequence_size = 3;
        } else if ((first & 0xF8U) == 0xF0U) {
            sequence_size = 4;
        }
        if (offset + sequence_size > bytes.size()) {
            break;
        }
        offset += sequence_size;
    }
    return offset;
}

std::unique_ptr<llama_sampler, decltype(&llama_sampler_free)> create_sampler(
        float temperature,
        float top_p,
        uint32_t seed) {
    auto params = llama_sampler_chain_default_params();
    params.no_perf = false;
    std::unique_ptr<llama_sampler, decltype(&llama_sampler_free)> sampler(
        llama_sampler_chain_init(params),
        llama_sampler_free);
    if (temperature <= 0.0f) {
        llama_sampler_chain_add(sampler.get(), llama_sampler_init_greedy());
    } else {
        llama_sampler_chain_add(
            sampler.get(),
            llama_sampler_init_top_p(std::clamp(top_p, 0.0f, 1.0f), 1));
        llama_sampler_chain_add(sampler.get(), llama_sampler_init_temp(temperature));
        llama_sampler_chain_add(sampler.get(), llama_sampler_init_dist(seed));
    }
    return sampler;
}

}  // namespace

extern "C" JNIEXPORT jstring JNICALL
Java_com_manishm_mobilespec_llama_JniNativeBindings_nativeCapabilities(
        JNIEnv * env,
        jobject /* self */) {
    initialize_backend();
    const std::string detail =
        std::string("CPU; ") + llama_print_system_info() +
        "; serialized queue; native monotonic timing; cancellation; phase-aware threads";
    return env->NewStringUTF(detail.c_str());
}

extern "C" JNIEXPORT jlong JNICALL
Java_com_manishm_mobilespec_llama_JniNativeBindings_nativeCreateSession(
        JNIEnv * env,
        jobject /* self */,
        jstring model_path,
        jint context_size,
        jint baseline_decode_threads,
        jint baseline_prefill_threads,
        jint optimized_decode_threads,
        jint optimized_prefill_threads,
        jint backend,
        jboolean use_mmap) {
    initialize_backend();
    if (backend != 0) {
        throw_illegal_state(env, "This APK contains the CPU backend only");
        return 0;
    }

    const std::string path = from_jstring(env, model_path);
    if (path.empty()) {
        throw_illegal_state(env, "Model path is empty");
        return 0;
    }

    std::unique_ptr<Session> session(new Session());
    llama_model_params model_params = llama_model_default_params();
    model_params.use_mmap = use_mmap == JNI_TRUE;
    model_params.n_gpu_layers = 0;
    session->model = llama_model_load_from_file(path.c_str(), model_params);
    if (session->model == nullptr) {
        throw_illegal_state(env, "llama.cpp failed to load the GGUF model");
        return 0;
    }

    llama_context_params context_params = llama_context_default_params();
    context_params.n_ctx = static_cast<uint32_t>(std::max(64, context_size));
    context_params.n_batch = std::min<uint32_t>(context_params.n_ctx, 512);
    context_params.n_ubatch = context_params.n_batch;
    if (baseline_decode_threads > 0 && baseline_prefill_threads > 0) {
        context_params.n_threads = baseline_decode_threads;
        context_params.n_threads_batch = baseline_prefill_threads;
    }
    context_params.no_perf = false;
    session->context = llama_init_from_model(session->model, context_params);
    if (session->context == nullptr) {
        throw_illegal_state(env, "llama.cpp failed to create an inference context");
        return 0;
    }
    session->vocab = llama_model_get_vocab(session->model);
    session->baseline_decode_threads = context_params.n_threads;
    session->baseline_prefill_threads = context_params.n_threads_batch;
    if (optimized_decode_threads > 0 && optimized_prefill_threads > 0) {
        session->optimized_decode_threads = optimized_decode_threads;
        session->optimized_prefill_threads = optimized_prefill_threads;
    }
    return static_cast<jlong>(reinterpret_cast<intptr_t>(session.release()));
}

extern "C" JNIEXPORT jlongArray JNICALL
Java_com_manishm_mobilespec_llama_JniNativeBindings_nativeGenerate(
        JNIEnv * env,
        jobject /* self */,
        jlong handle,
        jstring prompt_value,
        jint max_tokens,
        jfloat temperature,
        jfloat top_p,
        jlong seed,
        jint mode,
        jobject callback) {
    Session * session = from_handle(handle);
    if (session == nullptr || session->context == nullptr || session->vocab == nullptr) {
        throw_illegal_state(env, "Invalid native session");
        return nullptr;
    }
    if (callback == nullptr) {
        throw_illegal_state(env, "Token callback is required");
        return nullptr;
    }
    if (mode != 0 && mode != 1) {
        throw_illegal_state(env, "Unknown inference mode");
        return nullptr;
    }
    if (mode == 1 &&
        (session->optimized_decode_threads <= 0 || session->optimized_prefill_threads <= 0)) {
        throw_illegal_state(env, "Optimized mode requires a verified phase policy");
        return nullptr;
    }

    std::lock_guard<std::mutex> lock(session->generation_mutex);
    session->cancelled.store(false, std::memory_order_release);
    llama_memory_clear(llama_get_memory(session->context), true);
    const int decode_threads = mode == 1
        ? session->optimized_decode_threads
        : session->baseline_decode_threads;
    const int prefill_threads = mode == 1
        ? session->optimized_prefill_threads
        : session->baseline_prefill_threads;
    llama_set_n_threads(session->context, decode_threads, prefill_threads);

    const std::string prompt = from_jstring(env, prompt_value);
    const int prompt_count = -llama_tokenize(
        session->vocab,
        prompt.c_str(),
        static_cast<int32_t>(prompt.size()),
        nullptr,
        0,
        true,
        true);
    if (prompt_count <= 0) {
        throw_illegal_state(env, "Prompt tokenization failed");
        return nullptr;
    }
    if (prompt_count + std::max(0, max_tokens) > static_cast<int>(llama_n_ctx(session->context))) {
        throw_illegal_state(env, "Prompt plus output exceeds the configured context");
        return nullptr;
    }

    std::vector<llama_token> prompt_tokens(static_cast<size_t>(prompt_count));
    if (llama_tokenize(
            session->vocab,
            prompt.c_str(),
            static_cast<int32_t>(prompt.size()),
            prompt_tokens.data(),
            prompt_count,
            true,
            true) < 0) {
        throw_illegal_state(env, "Prompt tokenization failed");
        return nullptr;
    }

    const jclass callback_class = env->GetObjectClass(callback);
    const jmethodID on_token = callback_class == nullptr
        ? nullptr
        : env->GetMethodID(callback_class, "onToken", "(Ljava/lang/String;IJ)V");
    if (on_token == nullptr) {
        throw_illegal_state(env, "NativeTokenCallback ABI mismatch");
        return nullptr;
    }
    const jclass string_class = env->FindClass("java/lang/String");
    const jmethodID string_constructor = string_class == nullptr
        ? nullptr
        : env->GetMethodID(string_class, "<init>", "([BLjava/lang/String;)V");
    const jstring utf8_name = env->NewStringUTF("UTF-8");
    if (string_constructor == nullptr || utf8_name == nullptr) {
        throw_illegal_state(env, "Unable to initialize UTF-8 token conversion");
        return nullptr;
    }

    auto sampler = create_sampler(
        temperature,
        top_p,
        static_cast<uint32_t>(seed));
    llama_batch batch = llama_batch_get_one(prompt_tokens.data(), prompt_count);
    const int64_t total_start_us = ggml_time_us();
    if (llama_decode(session->context, batch) != 0) {
        throw_illegal_state(env, "llama.cpp failed during prompt evaluation");
        return nullptr;
    }

    const int64_t decode_start_us = ggml_time_us();
    int generated = 0;
    int64_t first_token_us = 0;
    std::string pending_utf8;
    while (generated < std::max(0, max_tokens) &&
           !session->cancelled.load(std::memory_order_acquire)) {
        // At this pin llama_sampler_sample() samples and accepts. Calling
        // llama_sampler_accept() again would update sampler state twice.
        llama_token token = llama_sampler_sample(sampler.get(), session->context, -1);
        if (llama_vocab_is_eog(session->vocab, token)) {
            break;
        }

        pending_utf8 += token_piece(session->vocab, token);
        const int64_t now_us = ggml_time_us();
        if (first_token_us == 0) {
            first_token_us = now_us;
        }
        const size_t complete_size = complete_utf8_prefix_size(pending_utf8);
        if (complete_size > 0) {
            const jstring text = new_utf8_string(
                env,
                pending_utf8.substr(0, complete_size),
                string_class,
                string_constructor,
                utf8_name);
            if (text == nullptr) {
                return nullptr;
            }
            env->CallVoidMethod(
                callback,
                on_token,
                text,
                static_cast<jint>(generated),
                static_cast<jlong>(now_us - total_start_us));
            env->DeleteLocalRef(text);
            if (env->ExceptionCheck()) {
                return nullptr;
            }
            pending_utf8.erase(0, complete_size);
        }

        generated++;
        if (generated >= max_tokens) {
            break;
        }
        batch = llama_batch_get_one(&token, 1);
        if (llama_decode(session->context, batch) != 0) {
            throw_illegal_state(env, "llama.cpp failed during token generation");
            return nullptr;
        }
    }

    const int64_t end_us = ggml_time_us();
    if (!pending_utf8.empty() && !session->cancelled.load(std::memory_order_acquire)) {
        const jstring text = new_utf8_string(
            env,
            pending_utf8,
            string_class,
            string_constructor,
            utf8_name);
        if (text == nullptr) {
            return nullptr;
        }
        env->CallVoidMethod(
            callback,
            on_token,
            text,
            static_cast<jint>(std::max(0, generated - 1)),
            static_cast<jlong>(end_us - total_start_us));
        env->DeleteLocalRef(text);
        if (env->ExceptionCheck()) {
            return nullptr;
        }
    }
    env->DeleteLocalRef(utf8_name);
    env->DeleteLocalRef(string_class);
    const jlong timing[] = {
        static_cast<jlong>(prompt_count),
        static_cast<jlong>(generated),
        static_cast<jlong>(first_token_us == 0 ? end_us - total_start_us
                                               : first_token_us - total_start_us),
        static_cast<jlong>(end_us - total_start_us),
        static_cast<jlong>(end_us - decode_start_us),
    };
    jlongArray result = env->NewLongArray(5);
    env->SetLongArrayRegion(result, 0, 5, timing);
    return result;
}

extern "C" JNIEXPORT void JNICALL
Java_com_manishm_mobilespec_llama_JniNativeBindings_nativeCancel(
        JNIEnv * /* env */,
        jobject /* self */,
        jlong handle) {
    Session * session = from_handle(handle);
    if (session != nullptr) {
        session->cancelled.store(true, std::memory_order_release);
    }
}

extern "C" JNIEXPORT void JNICALL
Java_com_manishm_mobilespec_llama_JniNativeBindings_nativeDestroySession(
        JNIEnv * /* env */,
        jobject /* self */,
        jlong handle) {
    delete from_handle(handle);
}
