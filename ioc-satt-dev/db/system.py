from caproto import ChannelType
from caproto.server import PVGroup, pvproperty
from caproto.server.autosave import autosaved

from .. import calculator
from . import util
from .util import monitor_pvs


class SystemGroup(PVGroup):
    """
    PV group for attenuator system-spanning information.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # TODO: this could be done by wrapping SystemGroup
        for obj in (self.best_config, self.active_config):
            util.hack_max_length_of_channeldata(obj,
                                                [0] * self.parent.num_filters)

    calculated_transmission = pvproperty(
        value=0.1,
        name='T_CALC',
        record='ao',
        upper_alarm_limit=1.0,
        lower_alarm_limit=0.0,
        read_only=True,
        doc='Calculated transmission (all blades)'
    )

    calculated_transmission_3omega = pvproperty(
        name='T_3OMEGA',
        value=0.5,
        upper_alarm_limit=1.0,
        lower_alarm_limit=0.0,
        read_only=True,
        doc='Calculated 3omega transmission (all blades)'
    )

    calculated_transmission_error = pvproperty(
        value=0.1,
        name='T_CALC_ERROR',
        record='ao',
        upper_alarm_limit=1.0,
        lower_alarm_limit=0.0,
        read_only=True,
        doc='Calculated transmission error'
    )

    running = pvproperty(
        value='False',
        name='Running',
        record='bo',
        enum_strings=['False', 'True'],
        read_only=True,
        doc='The system is running',
        dtype=ChannelType.ENUM
    )

    mirror_in = pvproperty(
        value='False',
        name='MIRROR_IN',
        record='bo',
        enum_strings=['False', 'True'],
        read_only=True,
        doc='The inspection mirror is in',
        dtype=ChannelType.ENUM
    )

    calc_mode = pvproperty(
        value='Floor',
        name='CalcMode',
        record='bo',
        enum_strings=['Floor', 'Ceiling'],
        read_only=False,
        doc='Mode for selecting floor or ceiling transmission estimation',
        dtype=ChannelType.ENUM
    )

    energy_source = pvproperty(
        value='Actual',
        name='EnergySource',
        record='bo',
        enum_strings=['Actual', 'Custom'],
        read_only=False,
        doc='Choose the source of photon energy',
        dtype=ChannelType.ENUM,
    )

    best_config = pvproperty(
        name='BestConfiguration_RBV',
        value=0,
        max_length=1,
        read_only=True
    )

    active_config = pvproperty(
        name='ActiveConfiguration_RBV',
        value=0,
        max_length=1,
        read_only=True
    )

    energy_actual = pvproperty(
        name='ActualPhotonEnergy_RBV',
        value=0.0,
        read_only=True,
        units='eV'
    )

    energy_custom = pvproperty(
        name='CustomPhotonEnergy',
        value=0.0,
        read_only=False,
        units='eV',
        lower_ctrl_limit=100.0,
        upper_ctrl_limit=30000.0,
    )

    energy_calc = pvproperty(
        name='LastPhotonEnergy_RBV',
        value=0.0,
        read_only=True,
        units='eV',
        doc='Energy that was used for the calculation.'
    )

    @energy_actual.startup
    async def energy_actual(self, instance, async_lib):
        """Update beam energy and calculated values."""
        pvname = self.parent.monitor_pvnames['ev']
        async for event, pv, data in monitor_pvs(pvname, async_lib=async_lib):
            if event == 'connection':
                self.log.info('%s %s', pv, data)
                continue

            eV = data.data[0]
            self.log.debug('Photon energy changed: %s', eV)

            if instance.value != eV:
                self.log.info("Photon energy changed to %s eV.", eV)
                await instance.write(eV)

        return eV

    desired_transmission = autosaved(
        pvproperty(
            name='DesiredTransmission',
            value=0.5,
            lower_ctrl_limit=0.0,
            upper_ctrl_limit=1.0,
            doc='Desired transmission value',
        )
    )

    run = pvproperty(
        value='False',
        name='Run',
        record='bo',
        enum_strings=['False', 'True'],
        doc='Run calculation',
        dtype=ChannelType.ENUM
    )

    async def run_calculation(self):
        energy = {
            'Actual': self.energy_actual.value,
            'Custom': self.energy_custom.value,
        }[self.energy_source.value]

        await self.energy_calc.write(energy)

        # Update all of the filters first, to determine their transmission
        # at this energy
        for filter in self.parent.filters.values():
            await filter.set_photon_energy(energy)

        await self.calculated_transmission.write(
            self.parent.calculate_transmission()
        )
        await self.calculated_transmission_3omega.write(
            self.parent.calculate_transmission_3omega()
        )

        # Using the above-calculated transmissions, find the best configuration
        config = calculator.get_best_config(
            all_transmissions=self.parent.all_transmissions,
            t_des=self.desired_transmission.value,
            mode=self.calc_mode.value
        )
        await self.best_config.write(config.filter_states)
        await self.calculated_transmission_error.write(
            config.transmission - self.desired_transmission.value
        )
        self.log.info(
            'Energy %s eV %s transmission desired %.2g estimated %.2g '
            '(delta %.3g) configuration: %s',
            energy,
            self.calc_mode.value,
            self.desired_transmission.value,
            config.transmission,
            self.calculated_transmission_error.value,
            config.filter_states,
        )

    @run.putter
    async def run(self, instance, value):
        if value == 'False':
            return

        try:
            await self.run_calculation()
        except Exception:
            self.log.exception('update_config failed?')

    # RUN.PROC -> run = 1
    util.process_writes_value(run, value=1)
