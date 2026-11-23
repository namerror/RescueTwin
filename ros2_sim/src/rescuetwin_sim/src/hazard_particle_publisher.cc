#include <cmath>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <utility>

#include <gz/msgs/particle_emitter.pb.h>
#include <gz/transport/Node.hh>

int main()
{
  gz::transport::Node node;
  std::map<std::string, gz::transport::Node::Publisher> publishers;
  std::string line;
  while (std::getline(std::cin, line))
  {
    std::istringstream input(line);
    std::string effect;
    unsigned int slot;
    double x, y, z, spread, width, height, rate;
    if (!(input >> effect >> slot >> x >> y >> z >> spread >> width >> height >> rate))
      return 1;

    const auto name = effect + "_" + std::to_string(slot);
    auto publisher = publishers.find(name);
    if (publisher == publishers.end())
    {
      auto channel = node.Advertise<gz::msgs::ParticleEmitter>(
          "/rescuetwin/hazard_effects/" + name);
      if (!channel)
        return 1;
      publisher = publishers.emplace(name, std::move(channel)).first;
    }

    gz::msgs::ParticleEmitter command;
    command.mutable_emitting()->set_data(rate > 0);
    if (rate > 0)
    {
      command.mutable_rate()->set_data(rate);
      auto *pose = command.mutable_pose();
      pose->mutable_position()->set_x(x);
      pose->mutable_position()->set_y(y);
      pose->mutable_position()->set_z(z);
      pose->mutable_orientation()->set_y(-std::sqrt(.5));
      pose->mutable_orientation()->set_w(std::sqrt(.5));
      // The emitter's +X axis points up. Keep its source footprint compact.
      command.mutable_size()->set_x(.04);
      command.mutable_size()->set_y(spread);
      command.mutable_size()->set_z(spread);
      command.mutable_particle_size()->set_x(width);
      command.mutable_particle_size()->set_y(height);
      command.mutable_particle_size()->set_z(width);
    }
    publisher->second.Publish(command);
  }
  return 0;
}
