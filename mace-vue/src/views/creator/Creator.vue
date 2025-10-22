<template>
  <v-container>
    <v-row>
      <Toolbox 
      v-on:grid_changed="update_grid"
      v-on:range_changed="update_range"
      v-on:save="save"
      v-on:load="load"
      v-on:clear="clear"
      />
    </v-row>
    <v-row>
      <v-col>
        <v-chip class="elevation-3" label>x={{ current_pos.x | to_int}}</v-chip>
        <v-chip class="elevation-3" label>y={{ current_pos.y | to_int}}</v-chip>
      </v-col>
    </v-row>
    <v-row
      align="center"
      justify="space-around">
      <v-col cols='8' ref='scenarioCanvas'>
        <v-stage ref="stage"
          :config="configKonva"
          @mousemove="updateCoordinates"
          @dragstart="handleDragstart"
          @dragend="handleDragend"
          @click="handleClick">
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
            <v-text
              :config="label"
              v-for="label in hlabels"
              v-bind:key="label._id">
            </v-text>
            <v-text
              :config="label"
              v-for="label in vlabels"
              v-bind:key="label._id">
            </v-text>
          </v-layer>
          <v-layer ref="layer">
            <v-circle
              :config="{
                x: item.x,
                y: item.y,
                radius: item.range,
                fill: '#111111',
                draggable: false,
                opacity: 0.05,
                stroke: '#1100FF',
                strokeWidth: 1
              }"
              v-for="item in list"
              :key="item.id">
            </v-circle>
            <v-circle
              v-for="item in list"
              :key="item.id"
              :config="item">
            </v-circle>
            <v-text
              :config="{
                  text: item._id,
                  fontSize: 9,
                  x: item.x-4,
                  y: item.y+8
                }"
              v-for="item in list"
              v-bind:key="item._id">
            </v-text>
          </v-layer>
        </v-stage>
      </v-col>
    </v-row>
  </v-container>
</template>

<script>
import Toolbox from '@/views/creator/Toolbox'


export default {
  name: 'Creator',
  components: {
    Toolbox
  },
  data () {
    return {
      filename: 'export.json',
      importFile: '',
      configKonva: {
        width: 1110,
        height: 600
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
      hlabels: [],
      vlabels: [],
      nodes: 0,
      networks: [
        {
          name : "fixed",
          prefix : "10.0.0.0/24",
          routing: "batman",
          settings: {
            range: "120",
            bandwidth: "54000000",
            delay: "1000",
            jitter: "0",
            error: "0",
            emane: {
              use : "True",
              unicastrate : "12",
              multicastrate : "12",
              mode : "1",
              fadingmodel : "nakagami"
            }
          }
        }
      ],
      settings: {
        omnet: "False",
        core: "True",
        dump: "True",
        number_of_nodes: 9,
        start_delay: 5,
        username: "bruno",
        disks_folder : "/mnt/pymace/",
        report_folder : "/home/mace/temp/reports/",
        runtime: 150,
        emane_location: [47.57917, -122.13232, 2.0],
        emane_scale: 150
      },
      type: "UTM",
      extra: {
          disks: "False",
          dump: {
            start : "False",
            delay : 35,
            duration: 15
          },
          mobility: "none",
          network: ["fixed"]
      }
    }
  },
  mounted () {
    this.createGrid()
    this.list = JSON.parse(localStorage.getItem('creator_nodes')) || []
  },
  methods: {
    handleDragstart () {
    },
    updateCoordinates (evt) {
      const stage = evt.target.getStage()
      this.current_pos = stage.getPointerPosition()
    },
    save () {
      var configfile = {}
      configfile.nodes = []
      configfile.settings = this.settings
      configfile.networks = this.networks
      this.list.forEach((element,i) => {
        var _node = {}
        _node.name = "uav" + i
        _node.settings = element
        _node.type = this.type
        _node.extra = this.extra
        _node.function = []
        configfile.nodes.push(_node)
      });
      // console.log(configfile)
      const blob = new Blob([JSON.stringify(configfile, null, 2)], {type: 'text/json'})
      const e = document.createEvent('MouseEvents')
      const a = document.createElement('a')
      a.download = this.filename
      a.href = window.URL.createObjectURL(blob)
      a.dataset.downloadurl = ['text/json', a.download, a.href].join(':')
      e.initEvent('click', true, false, window, 0, 0, 0, 0, 0, false, false, false, false, 0, null)
      a.dispatchEvent(e)
    },
    load (file) {
      var reader = new FileReader()
      reader.onload = (file) => {
        this.list = JSON.parse(file.target.result)
        const data = JSON.stringify(this.list)
        localStorage.setItem('creator_nodes', data)
      }
      reader.readAsText(file)
    },
    clear () {
      this.list = []
      this.nodes = 0
      this.importFile = ''
    },
    handleDragend (event) {
      if (((event.target.attrs.x > 0) && (event.target.attrs.x < this.configKonva.width)) && ((event.target.attrs.y > 0) && (event.target.attrs.y < this.configKonva.height))) {
        this.list[event.target.attrs._id].x = event.target.attrs.x
        this.list[event.target.attrs._id].y = event.target.attrs.y
        const data = JSON.stringify(this.list)
        localStorage.setItem('creator_nodes', data)
      } else {
        this.list.splice(event.target.attrs._id, 1)
        for (let i = event.target.attrs._id; i < this.list.length; i++) {
          this.list[i]._id = i
        }
        this.nodes--
        const data = JSON.stringify(this.list)
        localStorage.setItem('creator_nodes', data)
      }
    },
    handleClick (evt) {
      if (this.selectedType.title === 'Manual') {
        const stage = evt.target.getStage()
        const pos = stage.getPointerPosition()
        this.list.push({
          _id: this.nodes,
          x: pos.x,
          y: pos.y,
          fill: '#1100FF',
          stroke: 'black',
          strokeWidth: 0,
          shadowBlur: 1,
          shadowOffset: {x: 2, y: 2},
          shadowOpacity: 0.2,
          opacity: 0.8,
          draggable: true,
          radius: 6,
          shadowColor: 'black',
          type: 'node',
          range: this.range
        })
        this.nodes++
        const data = JSON.stringify(this.list)
        localStorage.setItem('creator_nodes', data)
      }
    },
    update_grid (event) {
      this.gridSize = event
    },
    update_range (event) {
      this.range = event
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
      this.hlabels = []
      for (let i = 0; i < this.configKonva.height / this.gridSize; i++) {
        this.hLines.push({
          _id: 'hL' + i,
          points: [0, Math.round(i * this.gridSize), Number(this.configKonva.width), Math.round(i * this.gridSize)],
          stroke: '#111111',
          strokeWidth: 0.3
        })
        this.hlabels.push({
          _id: 'hl' + i,
          text: Math.round(i * this.gridSize),
          x: 0,
          y: Math.round(i * this.gridSize),
          fontSize: 9
        })
      }
      this.vLines = []
      this.vlabels = []
      for (let j = 0; j < this.configKonva.width / this.gridSize; j++) {
        this.vLines.push({
          _id: 'vL' + j,
          points: [Math.round(j * this.gridSize), 0, Math.round(j * this.gridSize), Number(this.configKonva.height)],
          stroke: '#111111',
          strokeWidth: 0.2
        })
        this.vlabels.push({
          _id: 'vl' + j,
          text: Math.round(j * this.gridSize),
          x: Math.round(j * this.gridSize),
          y: 0,
          fontSize: 9
        })
      }
      this.configMapBack.width = this.configKonva.width
      this.configMapBack.height = this.configKonva.height
    }
  },
  created () {
  },
  watch: {
    configKonva: {
      handler () {
        this.createGrid()
      },
      deep: true
    },
    gridSize: function () {
      this.createGrid()
    },
    grid: function (newVal) {
      this.handleGrid(newVal)
    },
    list: function () {
      this.nodes = this.list.length
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