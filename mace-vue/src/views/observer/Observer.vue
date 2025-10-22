<template>
    <v-container ref='canv'>
      <v-row>
<!--         <v-btn small @click="show_settings = !show_settings" v-if="!show_settings">
          <v-icon small
          >
          mdi-cog
          </v-icon>
        </v-btn> -->
        <v-expand-transition>
          <v-col cols="2" class="ml-3 mr-3" v-show="true" >
            <v-row
            class="mt-3"
            justify="space-around">
<!--             <v-btn small @click="show_settings = !show_settings" v-if="show_settings" block>
              <v-icon small
              >
              mdi-cog
              </v-icon>
            </v-btn> -->
            <v-card
                class="mx-auto"
                outlined
                elevation="3"
            >
                <v-list-item three-line>
                <v-list-item-content>
                    <div class="overline mb-4">
                    Settings
                    </div>

                <v-list-item-subtitle>
                    <v-row>
                    <v-switch
                        class="pl-3"
                        v-model="options.nodeid"
                        label="Node ID"
                    ></v-switch>
                    <v-text-field
                        class="ml-5 mr-5"
                        label="Font size"
                        type="number"
                        v-model="options.nodeid_size"
                        placeholder="Placeholder"
                    ></v-text-field>
                    </v-row>

                </v-list-item-subtitle>
                <v-list-item-subtitle>

                </v-list-item-subtitle>
                </v-list-item-content>
                    <v-icon
                    large
                    >
                    mdi-cog
                    </v-icon>
                </v-list-item>
            </v-card>
            </v-row>
            <Networks
              v-on:options="update_network_options($event)"
              v-for="wlan in networks"
              :key="wlan.id"
              v-bind:wlan="wlan"
            />
          </v-col>
        </v-expand-transition>

        <v-col cols="8">
          <v-row
            class="mt-3"
            align="center"
            justify="space-around"
            ref='scenarioCanvas'>
              <v-stage ref="stage"
                @mousemove="updateCoordinates"
                @dragstart="handleDragStart"
                @dragend="handleDragEnd"
                :config="configKonva">
                <v-layer>
                  <v-rect :config="configMapBack"
                  />
                </v-layer>
                <v-layer ref="gridLayer">
                  <v-line
                    v-for="item in vLines"
                    :key="item.id"
                    :config="item">
                  </v-line>
                  <v-line
                    v-for="item in hLines"
                    :key="item.id"
                    :config="item">
                  </v-line>
                </v-layer>
                <v-layer ref="radio">
                  <div v-for="network in showRadio"
                       :key="network.id">
                    <v-circle
                      :config="{
                        x: item.x,
                        y: item.y,
                        radius: network.range,
                        fill: network.options.colour,
                        draggable: false,
                        opacity: 0.05,
                        stroke: '#FF0000',
                        strokeWidth: 1
                      }"
                      v-for="item in list"
                      :key="item.id">
                    </v-circle>
                  </div>
                  <div v-for="network in networks" :key="network.id">
                    <v-line
                      v-for="item in network.graphlines"
                      :key="item.id"
                      :config="item">
                    </v-line>
                  </div>
                </v-layer>
                <v-layer ref="nodes">
                  <div v-if="options.nodeid">
                    <v-text
                      :config="{
                          text: item._id,
                          fontSize: options.nodeid_size,
                          x: item.x-4,
                          y: item.y+12
                        }"
                      v-for="item in list"
                      v-bind:key="item._id">
                    </v-text>
                  </div>
                  <v-circle
                    :config="{
                      x: item.x - 7,
                      y: item.y - 7,
                      radius: item.radius/2,
                      fill: '#1100FF',
                      stroke: 'black',
                      strokeWidth: 0,
                      opacity: 0.8,
                      strokeWidth: 1
                    }"
                    v-for="item in list"
                    :key="item.id">
                  </v-circle>
                  <v-circle
                    :config="{
                      x: item.x + 7,
                      y: item.y + 7,
                      radius: item.radius/2,
                      fill: '#1100FF',
                      stroke: 'black',
                      strokeWidth: 0,
                      opacity: 0.8,
                      strokeWidth: 1
                    }"
                    v-for="item in list"
                    :key="item.id">
                  </v-circle>
                  <v-circle
                    :config="{
                      x: item.x + 7,
                      y: item.y - 7,
                      radius: item.radius/2,
                      fill: '#1100FF',
                      stroke: 'black',
                      strokeWidth: 0,
                      opacity: 0.8,
                      strokeWidth: 1
                    }"
                    v-for="item in list"
                    :key="item.id">
                  </v-circle>
                  <v-circle
                    :config="{
                      x: item.x - 7,
                      y: item.y + 7,
                      radius: item.radius/2,
                      fill: '#1100FF',
                      stroke: 'black',
                      strokeWidth: 0,
                      opacity: 0.8,
                      strokeWidth: 1
                    }"
                    v-for="item in list"
                    :key="item.id">
                  </v-circle>
                  <v-circle
                    v-for="item in list"
                    :key="item.id"
                    :config="{
                      draggable: false,
                      x: item.x,
                      y: item.y,
                      radius: item.radius*2,
                      stroke: 'black',
                      strokeWidth: 0,
                      opacity: 0.4,
                      strokeWidth: 1
                    }">
                  </v-circle>
                    <v-circle
                    v-for="item in list"
                    :key="item.id"
                    :config="item">
                  </v-circle>

                </v-layer>
              </v-stage>
          </v-row>
          <v-btn
            class="mt-4"
            @click="reset"
            color="yellow"
            light
            small
            block>
            Reset
          </v-btn>
          <v-row class="mt-2">
            <v-col>
              <v-chip class="elevation-3" label>x={{ current_pos.x | to_int}}</v-chip>
              <v-chip class="elevation-3" label>y={{ current_pos.y | to_int}}</v-chip>
            </v-col>
            <v-col>
              <v-chip
                v-if="isConnected"
                class="ma-2"
                label
                color="primary"
              >
                Live view
              </v-chip>
            </v-col>
        </v-row>
        </v-col>
      </v-row>

  </v-container>
</template>

<script>

import Networks from '@/views/observer/Networks'

export default {
  name: 'Observer',
  components: {
    Networks
  },
  data () {
    return {
      show_settings: false,
      networks: [],
      net_colours: [
        '#11FF11',
        '#1100FF',
        '#FF0011',
        '#AA00AA'
      ],
      leader: '',
      configKonva: {
        width: 1110,
        height: 600
      },
      configCanvas: {
        width: 750,
        height: 700,
        verticalAlign: 'middle'
      },
      selectedType: {
        id: 1,
        title: 'Manual'
      },
      current_pos: {
        x: 0,
        y: 0
      },
      gridSize: 50,
      configMapBack: {
        x: 0,
        y: 0,
        width: 0,
        height: 0,
        fill: '#FFFFFF',
        shadowBlur: 0
      },
      list: [],
      range: 120,
      hLines: [],
      vLines: [],
      graphlines: [],
      nodes: 0,
      wlan: {},
      isConnected: false,
      options: {
        graph: true,
        radio: true,
        nodeid: true,
        nodeid_size: 9
      },
      x_start: 0,
      y_start: 0
    }
  },
  mounted () {
    this.createGrid()
    this.configCanvas.width = this.$refs.canv.clientWidth
    this.configMapBack.width = this.$refs.canv.clientWidth
  },
  methods : {
    toggleSettings () {
      this.show_settings = !this.show_settings
    },
    handleDragStart (event) {
      this.x_start = event.target.attrs.x
      this.y_start = event.target.attrs.y
    },
    updateCoordinates (evt) {
      const stage = evt.target.getStage()
      this.current_pos = stage.getPointerPosition()
    },
    handleDragEnd (event) {
      // console.log(event.target)
      var node = {
        id: event.target.attrs._id,
        x: event.target.attrs.x,
        y: event.target.attrs.y
      }
      if (((event.target.attrs.x > 0) && (event.target.attrs.x < this.configKonva.width)) && ((event.target.attrs.y > 0) && (event.target.attrs.y < this.configKonva.height))) {
        this.list[event.target.attrs._id].x = event.target.attrs.x
        this.list[event.target.attrs._id].y = event.target.attrs.y
      } else {
        this.list[event.target.attrs._id].x = this.x_start
        this.list[event.target.attrs._id].y = this.y_start
      }
      this.emit_new_position(node)
      // this.list = []
    },
    emit_new_position (node) {
      this.$socket.client.emit('update_pos', {node: node});
    },
    reset () {
      this.$socket.client.emit('reset_pos');
    },
    createGrid () {
      if (this.configKonva.width > this.$refs.scenarioCanvas.clientWidth) {
        this.$nextTick(() => {
          this.configKonva.width = this.$refs.scenarioCanvas.clientWidth
          this.configMapBack.width = this.configKonva.width
        })
      } else if (this.configKonva.width < 1) {
        this.$nextTick(() => {
          this.configKonva.width = 1
          this.configMapBack.width = this.configKonva.width
        })
      }
      if (this.configKonva.height > this.$refs.scenarioCanvas.clientHeight) {
        this.$nextTick(() => {
          this.configKonva.height = this.$refs.scenarioCanvas.clientHeight
          this.configMapBack.height = this.configKonva.height
        })
      } else if (this.configKonva.height < 1) {
        this.$nextTick(() => {
          this.configKonva.height = 1
          this.configMapBack.height = this.configKonva.height
        })
      }
      this.hLines = []
      for (let i = 0; i < this.configKonva.height / this.gridSize; i++) {
        this.hLines.push({
          _id: i,
          points: [0, Math.round(i * this.gridSize), Number(this.configKonva.width), Math.round(i * this.gridSize)],
          stroke: '#111111',
          strokeWidth: 0.3
        })
      }
      this.vLines = []
      for (let j = 0; j < this.configKonva.width / this.gridSize; j++) {
        this.vLines.push({
          _id: j,
          points: [Math.round(j * this.gridSize), 0, Math.round(j * this.gridSize), Number(this.configKonva.height)],
          stroke: '#111111',
          strokeWidth: 0.2
        })
      }
      this.configMapBack.width = this.configKonva.width
      this.configMapBack.height = this.configKonva.height
    },
    update_canvas () {
      this.configCanvas.width = this.$refs.scenarioCanvas.clientWidth
      this.configMapBack.width = this.$refs.scenarioCanvas.clientWidth
      this.createGrid()
    },
    update_graph () {
      var counter = 0
      var netcounter = 0
      this.networks.forEach(net => {
        net.graphlines = []
        if (net.options.graph === true) {
          this.list.forEach(node1 => {
            this.list.forEach(node2 => {
              if (this.euclidean_distance(node1, node2) <= net.range) {
                net.graphlines.push({
                  _id: counter,
                  points: [node1.x, node1.y, node2.x, node2.y],
                  stroke: this.net_colours[netcounter],
                  strokeWidth: 0.5
                })
              }
              counter++
            })
          })
        }
        netcounter++
      });
    },
    euclidean_distance (node1, node2) {
      return Math.round(Math.sqrt(Math.pow(node1.x - node2.x,2) + Math.pow(node1.y - node2.y,2)))
    },
    update_network_options (network_options) {
      this.networks.forEach(element => {
        if (element.id === network_options.id) {
          //console.log(element)
          //element.options.graph = network_options.graph
          //element.options.radio = network_options.radio
        }
      });
    }
  },
  sockets: {
    nodes (_data) {
      if (!this.isConnected) {
        this.isConnected = true
      }
      var data = _data.data
      this.list = []
      for (let n = 0; n < data.nodes.length; n++) {
        var fill = '#1100FF'
        var x = data.nodes[n].position[0]
        var y = data.nodes[n].position[1]
        var z = data.nodes[n].position[2]
        if (n === parseInt(localStorage.getItem('leader'), 10)) {
          fill = '#FF0000'
        }
        this.list.push({
          _id: n,
          coreid: data.nodes[n].id,
          x: x,
          y: y,
          z: z,
          fill: fill,
          stroke: 'black',
          strokeWidth: 0,
          shadowBlur: 1,
          shadowOffset: {x: 2, y: 2},
          shadowOpacity: 0.2,
          opacity: 0.8,
          draggable: true,
          radius: 6,
          shadowColor: 'black',
          type: 'mote',
          range: data.nodes[n].range,
          networks: data.nodes[n].networks
        })
      }
      this.update_graph()
    },
    wlans (_data) {
      if (_data.data.length < this.networks.length) {
        this.networks = []
      }
      for (let n = 0; n < _data.data.length; n++) {
        if (!this.networks.find(x => x.id === _data.data[n].id)) {
          this.networks.push({
            id: _data.data[n].id,
            bandwidth: _data.data[n].bandwidth,
            delay: _data.data[n].delay,
            error: _data.data[n].error,
            jitter: _data.data[n].jitter,
            range: parseInt(_data.data[n].range,10),
            model:_data.data[n].model,
            options: {
              graph: false,
              radio: false,
              colour: this.net_colours[this.networks.length]
            },
            graphlines: []
          })
        } 

        this.networks.forEach(element => {
          if (element.id === _data.data[n].id) {
            element.bandwidth = _data.data[n].bandwidth
            element.delay = _data.data[n].delay
            element.error = _data.data[n].error
            element.jitter = _data.data[n].jitter
            element.range = parseInt(_data.data[n].range,10)
            element.model = _data.data[n].model
          }
        });
      }
    },
    connect () {
      this.isConnected = true
    },
    disconnect () {
      this.isConnected = false
    },
    digest (data) {
      localStorage.setItem('leader', data.data.leader)
      this.leader = data.data.leader
    }
  },
  watch: {
    configCanvas: {
      handler () {
        this.createGrid()
      }
    }
  },
  beforeDestroy () {
    clearInterval(this.timer)
  },
  created () {
    // window.addEventListener("resize", this.update_canvas)
  },
  computed: {
    bandwidth () {
      return Math.ceil(this.wlan.bandwidth / (1024 * 1024))
    },
    showRadio () {
      return this.networks.filter(i => i.options.radio === true)
    }
  },
  filters: {
    to_int: function (number) {
      return parseInt(number,10)
    }
  }
}
</script>

<style>

</style>