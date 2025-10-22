<template>
  <div>
    <v-row
        justify="space-around" class="mt-3">
        <v-card
            class="mx-auto"
            outlined
            elevation="3"
        >
            <v-list-item three-line>
            <v-list-item-content>
                <div class="overline mb-4">

                </div>
                <v-list-item-subtitle>Id: {{ wlan.id }}</v-list-item-subtitle>
                <v-list-item-subtitle>Model: {{ wlan.model }}</v-list-item-subtitle>
                <v-list-item-subtitle>Range: {{ wlan.range }}  m</v-list-item-subtitle>
                <v-list-item-subtitle>Bandwidth: {{ wlan.bandwidth }} bps</v-list-item-subtitle>
                <v-list-item-subtitle>Delay: {{ wlan.delay }} us</v-list-item-subtitle>
                <v-list-item-subtitle>Error rate: {{ wlan.error }} %</v-list-item-subtitle>
                <v-list-item-subtitle>Jitter: {{ wlan.jitter }} us</v-list-item-subtitle>
            </v-list-item-content>
                <v-icon
                large
                >
                mdi-antenna
                </v-icon>
            </v-list-item>
            <v-list-item-content>
                <v-switch
                    dense
                    class="pl-3 mt-n3"
                    v-model="wlan.options.graph"
                    label="Connection Line"
                ></v-switch>
                <v-switch
                    dense
                    class="pl-3 mt-n3"
                    v-model="wlan.options.radio"
                    label="Radio Range"
                ></v-switch>
            </v-list-item-content>
        </v-card>
    </v-row>

  </div>
</template>

<script>
export default {
  name: 'Networks',
  components: {
  },
  data () {
    return {
    }
  },
  props: {
    wlan: Object,
  },
  methods : {
    send_options () {
        //console.log(this.wlan)
        this.options.id = this.wlan.id
        this.$emit('options', this.options)
    }
  },
  watch: {
    'options.radio': function () {
      this.send_options()
    },
    'options.graph': function () {
      this.send_options()
    }
  },
}
</script>

<style>

</style>