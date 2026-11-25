import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import './tokens.css'
import './app.css'

document.documentElement.setAttribute('data-theme', 'purple')   // цвет акцента подставляется из профиля аккаунта после входа

createRoot(document.getElementById('root')).render(<App />)
