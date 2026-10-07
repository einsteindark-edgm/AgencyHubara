package com.hubara.operator.core.push

import com.hubara.operator.core.network.dto.DeviceRequest
import com.hubara.operator.core.network.dto.PushConfigDto

internal class FakePushApi : PushApi {
    var config = PushConfigDto()
    var failing = false
    val registered = mutableListOf<DeviceRequest>()
    val unregistered = mutableListOf<String>()

    override suspend fun config(): PushConfigDto = if (failing) error("sin red") else config
    override suspend fun register(body: DeviceRequest) {
        if (failing) error("sin red")
        registered += body
    }
    override suspend fun unregister(token: String) {
        unregistered += token
    }
}

internal class FakePushTransport : PushTransport {
    var token = "token-1"
    var available = true
    var deleted = 0
    val started = mutableListOf<PushOptions>()

    override fun start(options: PushOptions): Boolean {
        if (!available) return false
        started += options
        return true
    }
    override suspend fun token(): String = token
    override suspend fun deleteToken() {
        deleted++
    }
}
