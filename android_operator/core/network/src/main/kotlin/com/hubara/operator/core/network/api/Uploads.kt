package com.hubara.operator.core.network.api

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody

/** La foto como la espera el backend (`file: UploadFile = File()` en `POST …/media`). */
fun photoPart(fileName: String, bytes: ByteArray, mime: String = "image/jpeg"): MultipartBody.Part =
    MultipartBody.Part.createFormData("file", fileName, bytes.toRequestBody(mime.toMediaType()))
