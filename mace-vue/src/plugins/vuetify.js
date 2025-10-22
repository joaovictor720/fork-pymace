import Vue from 'vue'
import Vuetify from 'vuetify/lib'

import en from './en'

Vue.use(Vuetify)

export default new Vuetify({
  icons: {
    iconfont: 'mdi'
  },
  theme: {
    dark: false
  },
  lang: {
    locales: { en },
    current: 'en'
  }
})
