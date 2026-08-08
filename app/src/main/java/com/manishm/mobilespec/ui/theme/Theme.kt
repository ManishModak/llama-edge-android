package com.manishm.mobilespec.ui.theme

import android.app.Activity
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

private val DarkColors = darkColorScheme(
    primary = Color(0xFF9DCBFF),
    secondary = Color(0xFF9AD5C4),
    tertiary = Color(0xFFFFB4A9),
)

private val LightColors = lightColorScheme(
    primary = Color(0xFF00629F),
    secondary = Color(0xFF006B5B),
    tertiary = Color(0xFF98461F),
)

@Composable
fun MobileSpecTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val view = LocalView.current
    if (!view.isInEditMode) {
        val window = (view.context as Activity).window
        WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = !dark
    }
    MaterialTheme(
        colorScheme = if (dark) DarkColors else LightColors,
        content = content,
    )
}
