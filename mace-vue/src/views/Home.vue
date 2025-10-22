<template>
  <v-container >
    <v-row v-if="isConnected">
    </v-row>
    <v-row v-if="isConnected">
      <v-combobox
          v-model="selected_scenario"
          :items="sorted_scenarios"
          label="Pick a scenario"
          outlined
          dense
      ></v-combobox>
      <v-btn

        @click="load_scenario"
        small
        elevation="3"
        color="primary"
        class="ma-2"
        :disabled = disable_load
      >
        <v-icon left>
          mdi-folder-open
        </v-icon>
        Load
      </v-btn>
      <v-btn
        @click="run"
        small
        elevation="3"
        color="primary"
        class="ma-2"
        :disabled = !loaded
      >
        <v-icon left>
          mdi-play-box
        </v-icon>
        Run
      </v-btn>
      <v-btn
        @click="stop"
        small
        elevation="3"
        color="error"
        class="ma-2"
      >
        <v-icon left>
          mdi-skull
        </v-icon>
        Stop
      </v-btn>
      <v-btn
        @click="shutdown"
        small
        elevation="3"
        color="error"
        class="ma-2"
      >
        <v-icon left>
          mdi-skull
        </v-icon>
        Server Shutdown
      </v-btn>
      </v-row>
    <v-col justify="space-around" align="center">
      <img alt="Logo" src="../assets/logo.png">
      <div class="hello">

        <p>
          This is a WebApp GUI to interact with MACE - Mobile Ad-Hoc Computing Emulator<br>
          check out the
          <a href="https://github.com/brunobcfum/pymace" target="_blank" rel="noopener">MACE repository</a> for more information.
        </p>
      </div>
    </v-col>
  </v-container>
</template>

<script>
  export default {
    name: "Home",
    data: () => ({
      isConnected: false,
      emulation_data: {},
      selected_scenario: "",
      disable_load: true,
      loaded: false
    }),
    sockets: {
      connect () {
        this.isConnected = true
      },
      disconnect () {
        this.isConnected = false
      },
      pong () {
        this.isConnected = true
        this.requestData()
      },
      emulation_data (data) {
        // console.log(data.data)
        this.emulation_data = data.data
      }
    },
    mounted () {
      this.$socket.client.emit('pingServer')
    },
    methods: {
      requestData () {
        this.$socket.client.emit('request_data')
      },
      load_scenario () {
        this.$socket.client.emit('load', {scenario: this.selected_scenario});
        this.loaded = true
      },
      run () {
        this.$socket.client.emit('run');
      },
      shutdown () {
        this.$socket.client.emit('shutdown');
      },
      stop () {
        this.$socket.client.emit('stop');
      },
    },
    watch: {
      selected_scenario (after, ) {
        if (after !== "") {
          this.disable_load = false
        }
        else {
          this.disable_load = true
        }
      }
    },
    computed: {
      sorted_scenarios() {
        var _sorted_scenarios = this.emulation_data.scenarios || []
        _sorted_scenarios.sort()
        console.log(_sorted_scenarios)
/*         sorted_scenarios = sorted_scenarios.sort((a,b) => {
          let fa = a.toLowerCase(), fb = b.toLowerCase();
          if (fa < fb) {
            return -1
          }
          if (fa > fb) {
            return 1
          }
          return 0
        })
        console.log(this.emulation_data.scenarios) */
        return _sorted_scenarios
      }
    }
  }
</script>

<style>

</style>
