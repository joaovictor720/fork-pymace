<template>
  <v-toolbar dense dark>
    <v-menu
      open-on-hover
      bottom
      small
      offset-y
    >
      <template v-slot:activator="{ on, attrs }">
        <v-btn
          color="normal"
          small
          text
          v-bind="attrs"
          v-on="on"
        >
        {{ $vuetify.lang.t('$vuetify.menu.file') }}
        </v-btn>
      </template>

      <v-list>
        <v-list-item
          class="overline" 
          v-for="(item, index) in areas"
          :key="index"
          @click="open(item.route)"
        >
          <v-icon left>
            {{(item.icon)}}
          </v-icon>
          <v-list-item-title class="overline">{{ formMenu(item.title) }}</v-list-item-title>
        </v-list-item>
      </v-list>
    </v-menu>

    <v-btn
      small
      text
      color="normal"
      @click="openTimeline"
    >
      {{ $vuetify.lang.t('$vuetify.menu.previous') }}
    </v-btn>

    <v-btn
      text
      small
      color="normal"
      @click="openProgram"
    >
      {{ $vuetify.lang.t('$vuetify.menu.program') }}
    </v-btn>
    <v-btn
        small
        text
        color="normal"
        @click="openOrganization"
      >
        {{ $vuetify.lang.t('$vuetify.menu.organization') }}
    </v-btn>

  </v-toolbar>
</template>

<script>
export default {
  data () {
    return {
      areas: [
        { title: 'new',
          action: 'new',
          icon: 'mdi-recycle'},
        { title: 'open',
          route: 'open',
          icon: 'mdi-folder-open'},
        { title: 'save',
          route: 'save',
          icon: 'mdi-content-save'}
      ],
      net_fields: {
        name: '',
        prefix : "10.0.0.0/24",
        routing : "batman",
        settings : {
            range: 120,
            bandwidth : 54000000,
            delay: 1000,
            jitter: 0,
            error: 0,
            emane: {
                use : "True",
                unicastrate : 12,
                multicastrate : 12,
                mode : 1,
                fading: {
                  model : "nakagami"
                }
            }
        }
      },
      current_network: {},
      routing_options: [
        "batman",
        "none"
      ],
      fading_options: [
        "nakagami"
      ],
      mobility_options: [
        "random_waypoint",
        "none"
      ],
      function_options: [
        "terminal",
        "etcd",
        "disk"
      ],
      settings_fields: {
        "omnet": "False",
        "core": "True",
        "dump": "False",
        "number_of_nodes": 9,
        "start_delay": 5,
        "username": "username",
        "disks_folder" : "/mnt/pymace/"
      },
      node_options: {
        "function" : [],
            "type": "",
            "extra": {
                "disks": "False",
                "dump": {
                  "start" : "False",
                  "delay" : 10,
                  "duration": 100
                },
                "mobility": "none",
                "network": ["fixed"]
            }
      }
    }
  },
  methods: {
    formMenu (text) {
      var option = '$vuetify.menu.' + text
      return this.$vuetify.lang.t(option)
    }
  }
}
</script>

<style>

</style>