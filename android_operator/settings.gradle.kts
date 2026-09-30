pluginManagement {
    includeBuild("build-logic")
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "android_operator"

include(":app")
include(":core:model")
include(":core:navigation")
include(":core:network")
include(":core:database")
include(":core:data")
include(":core:designsystem")
include(":core:ui")
include(":core:push")
include(":widget:hot")
include(":feature:auth")
include(":feature:inbox")
include(":feature:chat")
include(":feature:fires")
include(":feature:orders")
