<template>
  <v-app-bar app dark dense>
    <v-app-bar-nav-icon @click="toggleMenu"></v-app-bar-nav-icon>
    <v-toolbar-title v-if="!drawer" class="grey--text">
      <span>mace</span>
      <span class="font-weight-bold">vue</span>
    </v-toolbar-title>
    <v-spacer></v-spacer>
    <v-toolbar-items>
      <v-switch
        class="pt-3"
        v-model="$vuetify.theme.dark"
        :label="$vuetify.lang.t('$vuetify.dark')"
      ></v-switch>
      <v-menu
        open-on-hover
        bottom
        offset-y
      >
        <template v-slot:activator="{ on, attrs }">
          <v-btn
            icon
            v-bind="attrs"
            v-on="on"
          >
            <v-icon>mdi-translate</v-icon>
          </v-btn>
        </template>

        <v-list>
          <v-list-item
            class="overline"  
          >
            {{ $vuetify.lang.t('$vuetify.menu.language') }}:
          </v-list-item>
          <v-list-item
            class="subtitle" 
            v-for="(item, index) in langs"
            :key="index"
            @click="set_lang(item.lang)"
          >
            <v-list-item-title>{{ item.title }}</v-list-item-title>
          </v-list-item>
        </v-list>
      </v-menu>
    </v-toolbar-items>
  </v-app-bar>
</template>

<script>
export default {
  data () {
    return {
      langs: [
        { title: 'English',
          lang: 'en'}
      ]
    }
  },
  methods: {
    toggleMenu () {
      this.$emit('toggleMenu')
    },
    set_lang (lang) {
      this.$vuetify.lang.current = lang
    },
  },
  props: {
    drawer: Boolean
  }
}
</script>

<style>

</style>
